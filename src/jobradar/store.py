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
    status      TEXT NOT NULL DEFAULT 'new',
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    notes       TEXT NOT NULL DEFAULT ''
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


def connect(db: str | pathlib.Path) -> sqlite3.Connection:
    path = pathlib.Path(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert(conn: sqlite3.Connection, jobs: Iterable[tuple[Job, Match]]) -> tuple[int, int]:
    """Insert new postings; merge sources/score on existing ones. Returns (new, seen)."""
    now = int(time.time())
    new = seen = 0
    for job, match in jobs:
        row = conn.execute("SELECT sources, score, decision, location FROM jobs WHERE uid = ?", (job.uid,)).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO jobs (uid, company, title, location, url, sources, published,
                       remote, description, score, decision, lane, reasons, scope, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.uid, job.company, job.title, job.location, job.url, job.source,
                    job.published, -1 if job.remote is None else int(job.remote),
                    job.description[:4000], match.score, match.decision, match.lane,
                    ",".join(match.reasons[:6]), match.scope, now, now,
                ),
            )
            new += 1
        else:
            sources = {s for s in (row["sources"] or "").split(",") if s}
            sources.add(job.source)
            keep_best = match.score > row["score"]
            locations = [part.strip() for part in (row["location"] or "").split(" / ") if part.strip()]
            if job.location and job.location not in locations:
                locations.append(job.location)
            conn.execute(
                """UPDATE jobs SET last_seen = ?, sources = ?, location = ?,
                       score = ?, decision = ?, lane = ?, reasons = ?, scope = ?,
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


def query(
    conn: sqlite3.Connection,
    status: str | None = None,
    decision: str | None = None,
    lane: str | None = None,
    min_score: int | None = None,
    since_seconds: int | None = None,
    search: str | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    where, params = ["1=1"], []
    if status:
        where.append("status = ?")
        params.append(status)
    if decision:
        where.append("decision = ?")
        params.append(decision)
    if lane:
        where.append("lane = ?")
        params.append(lane)
    if min_score is not None:
        where.append("score >= ?")
        params.append(min_score)
    if since_seconds:
        where.append("first_seen >= ?")
        params.append(int(time.time()) - since_seconds)
    if search:
        where.append("(company LIKE ? OR title LIKE ? OR location LIKE ?)")
        params += [f"%{search}%"] * 3
    params.append(limit)
    sql = (
        "SELECT * FROM jobs WHERE "
        + " AND ".join(where)
        + " ORDER BY decision = 'high' DESC, score DESC, first_seen DESC, title LIMIT ?"
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
    out: dict[str, dict[str, int]] = {"by_decision": {}, "by_status": {}}
    for row in rows:
        out["by_decision"][row["decision"]] = out["by_decision"].get(row["decision"], 0) + row["n"]
        out["by_status"][row["status"]] = out["by_status"].get(row["status"], 0) + row["n"]
    out["total"] = conn.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"]
    return out


def row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["reasons"] = [r for r in (data.get("reasons") or "").split(",") if r]
    data["sources"] = [s for s in (data.get("sources") or "").split(",") if s]
    return data


def dumps(rows: list[sqlite3.Row]) -> str:
    return json.dumps([row_to_dict(r) for r in rows], indent=2)
