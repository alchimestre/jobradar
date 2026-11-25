"""SQLite store: one row per posting, plus the source-fetch log."""

from __future__ import annotations

import json
import pathlib
import sqlite3
import time
from collections.abc import Iterable

from .models import Job, Match

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    uid         TEXT PRIMARY KEY,
    company     TEXT NOT NULL,
    title       TEXT NOT NULL,
    location    TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    sources     TEXT NOT NULL DEFAULT '',
    published   TEXT NOT NULL DEFAULT '',
    remote      INTEGER NOT NULL DEFAULT -1,
    description TEXT NOT NULL DEFAULT '',
    score       INTEGER NOT NULL DEFAULT 0,
    decision    TEXT NOT NULL DEFAULT 'low',
    lane        TEXT NOT NULL DEFAULT 'other',
    reasons     TEXT NOT NULL DEFAULT '',
    scope       TEXT NOT NULL DEFAULT 'ok',
    region      TEXT NOT NULL DEFAULT 'unknown',
    language    TEXT NOT NULL DEFAULT 'unknown',
    mode        TEXT NOT NULL DEFAULT 'unknown',
    country     TEXT NOT NULL DEFAULT 'unknown',
    status      TEXT NOT NULL DEFAULT 'new',
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    closed      TEXT NOT NULL DEFAULT '',
    checked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_first_seen ON jobs (first_seen DESC);
CREATE INDEX IF NOT EXISTS jobs_decision   ON jobs (decision);

CREATE TABLE IF NOT EXISTS fetches (
    source     TEXT PRIMARY KEY,
    last_fetch INTEGER NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    jobs       INTEGER NOT NULL DEFAULT 0
);
"""

STATUSES = ("new", "shortlist", "applied", "rejected", "ignored")
STALE_AFTER_SECONDS = 7 * 86400
LINK_CHECK_INTERVAL_SECONDS = 86400
CLOSING_STATUS_CODES = (404, 410)


def connect(db: str | pathlib.Path) -> sqlite3.Connection:
    path = pathlib.Path(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for column in ("region", "language", "mode", "country"):
        if column not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT 'unknown'")
    if "closed" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN closed TEXT NOT NULL DEFAULT ''")
    if "checked" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN checked INTEGER NOT NULL DEFAULT 0")
    # WAL + busy_timeout: the TUI reads on the main thread while workers write.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get(conn: sqlite3.Connection, uid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE uid = ?", (uid,)).fetchone()



def upsert(conn: sqlite3.Connection, jobs: Iterable[tuple[Job, Match]]) -> tuple[int, int]:
    """Insert new postings; merge sources/score on existing ones. Returns (new, seen)."""
    now = int(time.time())
    new = seen = 0
    for job, match in jobs:
        row = conn.execute("SELECT sources, score, decision, location FROM jobs WHERE uid = ?", (job.uid,)).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO jobs (uid, company, title, location, url, sources, published,
                       remote, description, score, decision, lane, reasons, scope, region, language,
                       mode, country, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.uid, job.company, job.title, job.location, job.url, job.source,
                    job.published, -1 if job.remote is None else int(job.remote),
                    job.description[:4000], match.score, match.decision, match.lane,
                    ",".join(match.reasons[:6]), match.scope, match.region, match.language,
                    match.mode, match.country, now, now,
                ),
            )
            new += 1
        else:
            sources = {s for s in (row["sources"] or "").split(",") if s}
            sources.add(job.source)
            # A scope gate is authoritative, not a score improvement: a posting that is out
            # of scope now must be recorded as out, even seen through a source that scores it
            # no higher. Without this, a rule change (JD language, region, target country)
            # never reaches rows already in the store.
            scope_changed = (match.decision == "out") != (row["decision"] == "out")
            keep_best = match.score > row["score"] or scope_changed
            locations = [part.strip() for part in (row["location"] or "").split(" / ") if part.strip()]
            if job.location and job.location not in locations:
                locations.append(job.location)
            conn.execute(
                """UPDATE jobs SET last_seen = ?, sources = ?, location = ?, closed = '',
                       score = ?, decision = ?, lane = ?, reasons = ?, scope = ?,
                       region = ?, language = ?, mode = ?, country = ?,
                       remote = ?, url = CASE WHEN url = '' THEN ? ELSE url END,
                       description = CASE WHEN description = '' THEN ? ELSE description END
                   WHERE uid = ?""",
                (
                    now,
                    ",".join(sorted(sources)),
                    " / ".join(locations),
                    match.score if keep_best else row["score"],
                    match.decision if keep_best else row["decision"],
                    match.lane, ",".join(match.reasons[:6]), match.scope,
                    match.region, match.language, match.mode, match.country,
                    -1 if job.remote is None else int(job.remote), job.url,
                    job.description[:4000], job.uid,
                ),
            )
            seen += 1
    conn.commit()
    return new, seen


def record_fetch(conn: sqlite3.Connection, source: str, count: int, error: str = "") -> None:
    conn.execute(
        """INSERT INTO fetches (source, last_fetch, last_error, jobs) VALUES (?,?,?,?)
           ON CONFLICT(source) DO UPDATE SET last_fetch=?, last_error=?, jobs=?""",
        (source, int(time.time()), error, count, int(time.time()), error, count),
    )
    conn.commit()


def last_fetch(conn: sqlite3.Connection, source: str) -> int:
    row = conn.execute("SELECT last_fetch FROM fetches WHERE source = ?", (source,)).fetchone()
    return int(row["last_fetch"]) if row else 0


def close_missing(conn: sqlite3.Connection, source: str, live_uids: set[str]) -> int:
    """Close open postings a full employer board no longer lists. Status is left untouched."""
    rows = conn.execute(
        "SELECT uid, sources FROM jobs WHERE closed = '' AND sources LIKE ?", (f"%{source}%",)
    ).fetchall()
    gone = [r["uid"] for r in rows if source in r["sources"].split(",") and r["uid"] not in live_uids]
    conn.executemany("UPDATE jobs SET closed = ? WHERE uid = ?", [(f"gone from {source}", uid) for uid in gone])
    conn.commit()
    return len(gone)


def due_link_checks(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """Open, worth-applying postings whose link was not checked in the last day, oldest check first."""
    return conn.execute(
        """SELECT uid, url FROM jobs
           WHERE closed = '' AND url != '' AND decision IN ('high', 'review') AND checked < ?
           ORDER BY checked LIMIT ?""",
        (int(time.time()) - LINK_CHECK_INTERVAL_SECONDS, limit),
    ).fetchall()


def record_link_check(conn: sqlite3.Connection, uid: str, status_code: int | None) -> bool:
    """Store one link check; only a 404 or 410 closes the posting. Returns True when it closed."""
    closes = status_code in CLOSING_STATUS_CODES
    conn.execute(
        "UPDATE jobs SET checked = ?, closed = CASE WHEN ? THEN ? ELSE closed END WHERE uid = ?",
        (int(time.time()), closes, f"link answered HTTP {status_code}", uid),
    )
    conn.commit()
    return closes


def query(
    conn: sqlite3.Connection,
    status: str | None = None,
    decision: str | None = None,
    lane: str | None = None,
    min_score: int | None = None,
    since_seconds: int | None = None,
    search: str | None = None,
    reason: str | None = None,
    region: str | None = None,
    language: str | None = None,
    mode: str | None = None,
    country: str | None = None,
    include_out: bool = False,
    include_closed: bool = False,
    limit: int = 500,
) -> list[sqlite3.Row]:
    where, params = ["1=1"], []
    # Out-of-scope rows (wrong region, wrong JD language) are stored for inspection but
    # never shown by default; asking for them explicitly is how you audit the filter.
    if decision:
        where.append("decision = ?")
        params.append(decision)
    elif not include_out:
        where.append("decision != 'out'")
    if not include_closed:
        where.append("closed = ''")
    if status:
        where.append("status = ?")
        params.append(status)
    if lane:
        where.append("lane = ?")
        params.append(lane)
    if region:
        where.append("region = ?")
        params.append(region)
    if language:
        where.append("language = ?")
        params.append(language)
    if mode:
        where.append("mode = ?")
        params.append(mode)
    if country:
        where.append("country = ?")
        params.append(country)
    if min_score is not None:
        where.append("score >= ?")
        params.append(min_score)
    if since_seconds:
        where.append("first_seen >= ?")
        params.append(int(time.time()) - since_seconds)
    if search:
        where.append("(company LIKE ? OR title LIKE ? OR location LIKE ?)")
        params += [f"%{search}%"] * 3
    if reason:
        where.append("reasons LIKE ?")
        params.append(f"%{reason}%")
    params.append(limit)
    sql = (
        "SELECT * FROM jobs WHERE "
        + " AND ".join(where)
        # Within a decision: postings the last scans still list, then France, then EU/EMEA/AU.
        # shortcut: "unseen for 7 days before the newest sighting" stands in for "closed";
        # compare against each source's own last fetch if single-source scans become routine.
        + " ORDER BY decision = 'high' DESC,"
        f" last_seen < (SELECT MAX(last_seen) FROM jobs) - {STALE_AFTER_SECONDS},"
        " country = 'fr' DESC,"
        " CASE region WHEN 'eu' THEN 0 WHEN 'emea' THEN 1 WHEN 'au' THEN 2 ELSE 3 END,"
        " score DESC, first_seen DESC, title LIMIT ?"
    )
    return conn.execute(sql, params).fetchall()


def set_status(conn: sqlite3.Connection, uid: str, status: str, note: str = "") -> bool:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    cur = conn.execute(
        "UPDATE jobs SET status = ?, notes = CASE WHEN ? = '' THEN notes ELSE ? END WHERE uid = ?",
        (status, note, note, uid),
    )
    conn.commit()
    return cur.rowcount > 0


def counts(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT decision, status, COUNT(*) n FROM jobs GROUP BY decision, status"
    ).fetchall()
    out: dict[str, dict[str, int]] = {"by_decision": {}, "by_status": {}, "by_region": {}, "by_language": {}}
    for row in rows:
        out["by_decision"][row["decision"]] = out["by_decision"].get(row["decision"], 0) + row["n"]
        out["by_status"][row["status"]] = out["by_status"].get(row["status"], 0) + row["n"]
    for column, key in (("region", "by_region"), ("language", "by_language")):
        for row in conn.execute(f"SELECT {column} k, COUNT(*) n FROM jobs GROUP BY {column}"):
            out[key][row["k"]] = row["n"]
    total = conn.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"]
    out_of_scope = out["by_decision"].get("out", 0)
    return {
        **out,
        "total": total,
        "out_of_scope": out_of_scope,
        "in_scope": total - out_of_scope,
    }


def row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["reasons"] = [r for r in (data.get("reasons") or "").split(",") if r]
    data["sources"] = [s for s in (data.get("sources") or "").split(",") if s]
    return data


def dumps(rows: list[sqlite3.Row]) -> str:
    return json.dumps([row_to_dict(r) for r in rows], indent=2)
