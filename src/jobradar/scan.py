"""Fetch every configured source, learn new ATS boards, score and store."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Callable

from . import store
from .config import load_companies, save_companies
from .filters import Scorer
from .net import Fetcher
from .resolve import learn_from_url
from .sources import FEED_FETCHERS, FETCHERS


@dataclass
class ScanReport:
    new: int = 0
    seen: int = 0
    scored: int = 0
    sources: list[dict] = field(default_factory=list)
    learned: list[dict] = field(default_factory=list)
    companies: int = 0
    feeds: int = 0

    @property
    def errors(self) -> list[dict]:
        return [s for s in self.sources if s["error"]]

    def summary(self) -> str:
        line = (
            f"{self.sources_ok}/{len(self.sources)} sources ok · "
            f"+{self.new} new · {self.seen} repeat · {self.scored} matched"
        )
        if self.learned:
            line += f" · learned {len(self.learned)} ATS board(s)"
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
        feed_fn = FEED_FETCHERS.get(spec.get("type", ""))
        if feed_fn is None:
            continue
        out.append((f"{spec['type']}:{spec['name']}", lambda f, fn=feed_fn, s=spec: fn(f, s)))
    return out


def scan(
    cfg: dict,
    conn: sqlite3.Connection,
    only: str | None = None,
    learn: bool = True,
    cache_minutes: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> ScanReport:
    import time

    def announce(message: str) -> None:
        if progress:
            progress(message)

    scorer = Scorer(cfg["title_rules"], cfg["negative_rules"])
    companies = [c for c in load_companies() if c.get("enabled", True)]
    cache = int(cfg.get("cache_minutes", 0) if cache_minutes is None else cache_minutes)
    report = ScanReport(companies=len(companies), feeds=len(cfg.get("feeds") or []))
    known = {(c["ats"], c["token"]) for c in companies}
    sources = _source_list(cfg, companies)
    if only:
        sources = [s for s in sources if s[0] == only or s[0].startswith(only)]
    fetcher = Fetcher(cfg["timeout"], cfg["user_agent"])
    batch: list[tuple] = []
    try:
        for source_id, fetch in sources:
            if cache and (time.time() - store.last_fetch(conn, source_id)) < cache * 60:
                report.sources.append({"source": source_id, "jobs": 0, "error": "", "cached": True})
                continue
            announce(f"fetching {source_id}")
            try:
                jobs = fetch(fetcher)
                error = ""
            except Exception as exc:  # a broken board must never abort the scan
                jobs, error = [], f"{type(exc).__name__}: {exc}"[:200]
                store.record_fetch(conn, source_id, 0, error)
                report.sources.append({"source": source_id, "jobs": 0, "error": error, "cached": False})
                continue
            store.record_fetch(conn, source_id, len(jobs), "")
            report.sources.append({"source": source_id, "jobs": len(jobs), "error": "", "cached": False})
            for job in jobs:
                if learn:
                    found = learn_from_url(job.url)
                    if found and found not in known:
                        known.add(found)
                        companies.append(
                            {
                                "name": job.company or found[1],
                                "ats": found[0],
                                "token": found[1],
                                "market": "",
                                "tier": "",
                                "auto": True,
                                "enabled": True,
                            }
                        )
                        report.learned.append({"ats": found[0], "token": found[1], "name": job.company, "from": job.url})
                match = scorer.match(job)
                if match.score < int(cfg.get("min_score", 3)):
                    continue
                report.scored += 1
                batch.append((job, match))
    finally:
        fetcher.close()

    new, seen = store.upsert(conn, batch)
    report.new, report.seen = new, seen
    if report.learned:
        save_companies(companies)
        announce(f"watchlist updated: +{len(report.learned)} board(s)")
    return report
