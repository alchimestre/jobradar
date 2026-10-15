"""Fetch every configured source, learn new ATS boards, score and store."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from . import store
from .config import load_companies, save_companies, unset_placeholders
from .filters import Scorer
from .net import Fetcher
from .resolve import board_name, learn_from_url
from .sources import FEED_FETCHERS, FETCHERS


@dataclass
class ScanReport:
    new: int = 0
    seen: int = 0
    scored: int = 0
    out_of_scope: int = 0
    sources: list[dict] = field(default_factory=list)
    learned: list[dict] = field(default_factory=list)
    companies: int = 0
    feeds: int = 0
    unconfigured: list[dict] = field(default_factory=list)

    @property
    def errors(self) -> list[dict]:
        return [s for s in self.sources if s["error"]]

    def summary(self) -> str:
        line = (
            f"{self.sources_ok}/{len(self.sources)} sources ok · "
            f"+{self.new} new · {self.seen} repeat · {self.scored} matched"
        )
        if self.out_of_scope:
            line += f" · {self.out_of_scope} out of scope (region/language)"
        if self.learned:
            line += f" · learned {len(self.learned)} ATS board(s)"
        if self.unconfigured:
            names = sorted({name for entry in self.unconfigured for name in entry["missing"]})
            line += f" · {len(self.unconfigured)} source(s) need {', '.join(names)}"
        if self.errors:
            line += f" · {len(self.errors)} error(s)"
        return line

    @property
    def sources_ok(self) -> int:
        return len(self.sources) - len(self.errors)


def _source_list(cfg: dict, companies: list[dict]) -> list[tuple[str, Callable[[Fetcher], list]]]:
    out: list[tuple[str, Callable[[Fetcher], list]]] = []
    for company in companies:
        if not company.get("enabled", True):
            continue
        ats, token, name = company["ats"], company["token"], company["name"]
        if ats not in FETCHERS:
            continue
        fetcher_fn = FETCHERS[ats]
        out.append(
            (
                f"{ats}:{token}",
                lambda f, fn=fetcher_fn, t=token, n=name: fn(f, t, n),
            )
        )
    for spec in cfg.get("feeds") or []:
        if not spec.get("enabled", True):  # muted by hand, kept as a record of the source
            continue
        if unset_placeholders(spec):  # a source with no key configured is skipped, not failed
            continue
        feed_fn = FEED_FETCHERS.get(spec.get("type", ""))
        if feed_fn is None:
            continue
        out.append((f"{spec['type']}:{spec['name']}", lambda f, fn=feed_fn, s=spec: fn(f, s)))
    return out


def _scorer(cfg: dict) -> Scorer:
    """One place where the admission policy is assembled from config."""
    return Scorer(
        cfg["title_rules"],
        cfg["negative_rules"],
        allowed_languages=tuple(cfg.get("allowed_languages") or ("en", "fr", "unknown")),
        allowed_regions=tuple(cfg.get("allowed_regions") or ("eu", "emea", "au", "unknown")),
        target_countries=tuple(cfg.get("target_countries") or ("fr", "de", "ch", "nl")),
    )


def rescore(cfg: dict, conn: sqlite3.Connection, prune: bool = False) -> dict:
    """Re-score every stored posting from its stored fields (no network).

    Upsert only ever keeps the *best* score, so tightening a rule would otherwise
    leave inflated rows behind. `prune` deletes below-floor rows that were never acted on.
    """
    import time

    from .models import Job as _Job

    scorer = _scorer(cfg)
    floor = int(cfg.get("min_score", 3))
    rows = conn.execute("SELECT uid, company, title, location, url, remote, status, description FROM jobs").fetchall()
    changed = 0
    for row in rows:
        job = _Job(
            company=row["company"],
            title=row["title"],
            url=row["url"],
            source="rescore",
            location=row["location"],
            remote=None if row["remote"] < 0 else bool(row["remote"]),
            description=row["description"] or "",
        )
        match = scorer.match(job)
        conn.execute(
            "UPDATE jobs SET score=?, decision=?, lane=?, reasons=?, scope=?, region=?, language=?,"
            " mode=?, country=?, last_seen=? WHERE uid=?",
            (
                match.score, match.decision, match.lane, ",".join(match.reasons[:6]), match.scope,
                match.region, match.language, match.mode, match.country, int(time.time()), row["uid"],
            ),
        )
        changed += 1
    pruned = 0
    if prune:
        cur = conn.execute(
            "DELETE FROM jobs WHERE score < ? AND status = 'new' AND decision IN ('low', 'out')",
            (floor,),
        )
        pruned = cur.rowcount
    conn.commit()
    out_of_scope = conn.execute("SELECT COUNT(*) n FROM jobs WHERE decision = 'out'").fetchone()["n"]
    return {"rescored": changed, "pruned": pruned, "floor": floor, "out_of_scope": out_of_scope}


# ---------------------------------------------------------------- scan


def scan(
    cfg: dict,
    conn: sqlite3.Connection,
    only: str | None = None,
    learn: bool = True,
    cache_minutes: int | None = None,
    progress: Callable[[str], None] | None = None,
    workers: int | None = None,
) -> ScanReport:
    import time

    def announce(message: str) -> None:
        if progress:
            progress(message)

    scorer = _scorer(cfg)
    all_companies = load_companies()
    companies = [c for c in all_companies if c.get("enabled", True)]
    cache = int(cfg.get("cache_minutes", 0) if cache_minutes is None else cache_minutes)
    feeds = cfg.get("feeds") or []
    active = [spec for spec in feeds if spec.get("enabled", True)]
    pending = [
        {"source": f"{spec.get('type')}:{spec.get('name')}", "missing": unset_placeholders(spec)}
        for spec in active
    ]
    report = ScanReport(
        companies=len(companies),
        feeds=len(active) - len([entry for entry in pending if entry["missing"]]),
        unconfigured=[entry for entry in pending if entry["missing"]],
    )
    # Known boards include the disabled ones: `enabled: false` means "mute", not "re-discover".
    known = {(c["ats"].lower(), c["token"].lower()) for c in all_companies}
    sources = _source_list(cfg, companies)
    if only:
        sources = [s for s in sources if s[0] == only or s[0].startswith(only)]
    fetcher = Fetcher(cfg["timeout"], cfg["user_agent"])
    batch: list[tuple] = []
    to_fetch: list[tuple[int, str, Callable[[Fetcher], list]]] = []
    rows: list[dict | None] = [None] * len(sources)
    for position, (source_id, fetch) in enumerate(sources):
        if cache and (time.time() - store.last_fetch(conn, source_id)) < cache * 60:
            rows[position] = {"source": source_id, "jobs": 0, "error": "", "cached": True}
            continue
        to_fetch.append((position, source_id, fetch))

    # Sources are independent and network-bound, so they fetch in parallel. Scoring,
    # auto-learning and every SQLite call stay on this thread, in source order: one
    # connection, one writer, and a report whose rows do not depend on which fetch won.
    announce_guard = threading.Lock()

    def fetch_one(item: tuple[int, str, Callable[[Fetcher], list]]) -> tuple[int, str, list, str]:
        position, source_id, fetch = item
        with announce_guard:
            announce(f"fetching {source_id}")
        try:
            return position, source_id, fetch(fetcher), ""
        except Exception as exc:  # a broken board must never abort the scan
            return position, source_id, [], f"{type(exc).__name__}: {exc}"[:200]

    workers = max(1, int(cfg.get("scan_workers", 8) if workers is None else workers))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            fetched = list(pool.map(fetch_one, to_fetch))
    finally:
        fetcher.close()

    for position, source_id, jobs, error in fetched:
        rows[position] = {
            "source": source_id,
            "jobs": len(jobs),
            "error": error,
            "cached": False,
        }
    report.sources = [row for row in rows if row is not None]

    for _, source_id, jobs, error in fetched:
        store.record_fetch(conn, source_id, 0 if error else len(jobs), error)
        if error:
            continue
        for job in jobs:
            if learn:
                found = learn_from_url(job.url)
                if found and (found[0].lower(), found[1].lower()) not in known:
                    known.add((found[0].lower(), found[1].lower()))
                    entry = {
                        "name": board_name(job.company, found[1]),
                        "ats": found[0],
                        "token": found[1],
                        "market": "",
                        "tier": "",
                        "auto": True,
                        "enabled": True,
                    }
                    companies.append(entry)
                    all_companies.append(entry)
                    report.learned.append(
                        {"ats": found[0], "token": found[1], "name": job.company, "from": job.url}
                    )
            match = scorer.match(job)
            if match.decision == "out":
                report.out_of_scope += 1
            if match.score < int(cfg.get("min_score", 3)):
                continue
            report.scored += 1
            batch.append((job, match))

    new, seen = store.upsert(conn, batch)
    report.new, report.seen = new, seen
    if report.learned:
        save_companies(all_companies)
        announce(f"watchlist updated: +{len(report.learned)} board(s)")
    return report
