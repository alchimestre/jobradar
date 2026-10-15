"""jobradar command line."""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import webbrowser
from collections import Counter

from . import store
from .config import load_companies, load_config, root, save_companies
from .scan import scan


def _since(value: str | None) -> int | None:
    if not value:
        return None
    found = re.fullmatch(r"(\d+)([hdw])", value.strip().lower())
    if not found:
        raise SystemExit("--since expects e.g. 12h, 7d, 2w")
    amount, unit = int(found.group(1)), found.group(2)
    return amount * {"h": 3600, "d": 86400, "w": 604800}[unit]


def _db_path(cfg: dict) -> pathlib.Path:
    path = pathlib.Path(str(cfg["db"]))
    return path if path.is_absolute() else pathlib.Path(cfg["root"]) / path


def _table(rows, columns: list[tuple[str, int]], empty: str = "(no rows)") -> str:
    if not rows:
        return empty
    header = "  ".join(label.ljust(width)[:width] for label, width in columns)
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            "  ".join(
                str(row.get(key) or "")[:width].ljust(width) for key, width in [(c[0], c[1]) for c in columns]
            )
        )
    return "\n".join(lines)


def cmd_scan(args) -> int:
    cfg = load_config()
    conn = store.connect(_db_path(cfg))
    report = scan(
        cfg,
        conn,
        only=args.only,
        learn=not args.no_learn,
        cache_minutes=args.cache_minutes,
        workers=args.jobs,
        progress=(None if args.quiet else (lambda m: print(f"  · {m}", file=sys.stderr))),
    )
    print(report.summary())
    for row in report.learned:
        print(f"  learned: {row['ats']}:{row['token']}  (from {row['name']})")
    for row in report.errors:
        print(f"  error:   {row['source']}: {row['error']}", file=sys.stderr)
    if not args.quiet:
        # `jobs` is stringified because the renderer blanks a falsy value, and a board that
        # answers with no postings must not look like the failed rows beside it.
        print(
            _table(
                [dict(s, jobs=str(s.get("jobs", 0))) for s in report.sources if not s.get("cached")],
                [("source", 34), ("jobs", 6), ("error", 60)],
            )
        )
    return 0


def cmd_list(args) -> int:
    cfg = load_config()
    conn = store.connect(_db_path(cfg))
    rows = store.query(
        conn,
        status=args.status,
        decision=args.decision,
        lane=args.lane,
        min_score=args.min_score,
        since_seconds=_since(args.since),
        search=args.search,
        reason=getattr(args, "reason", None),
        region=getattr(args, "region", None),
        language=getattr(args, "language", None),
        mode=getattr(args, "mode", None),
        country=getattr(args, "country", None),
        include_out=getattr(args, "include_out", False),
        limit=args.limit,
    )
    if args.json:
        print(store.dumps(rows))
        return 0
    slim = [
        {
            "seen": _date_only(r["first_seen"]),
            "dec": r["decision"],
            "score": r["score"],
            "lane": r["lane"],
            "company": r["company"],
            "title": r["title"],
            "location": r["location"],
            "mode": r["mode"],
            "country": r["country"],
            "region": r["region"],
            "lang": r["language"],
            "status": r["status"],
            "uid": r["uid"],
        }
        for r in rows
    ]
    print(
        _table(
            slim,
            [
                ("seen", 10),
                ("dec", 6),
                ("score", 5),
                ("lane", 9),
                ("company", 18),
                ("title", 44),
                ("location", 20),
                ("mode", 8),
                ("country", 8),
                ("region", 9),
                ("lang", 5),
                ("status", 9),
                ("uid", 16),
            ],
        )
    )
    return 0


def _date_only(epoch: int) -> str:
    import time

    return time.strftime("%Y-%m-%d", time.localtime(epoch))


def cmd_show(args) -> int:
    cfg = load_config()
    conn = store.connect(_db_path(cfg))
    rows = [r for r in store.query(conn, limit=2000) if r["uid"] == args.uid]
    if not rows:
        print(f"no job with uid {args.uid}", file=sys.stderr)
        return 1
    import json

    print(json.dumps(store.row_to_dict(rows[0]), indent=2))
    return 0


def cmd_mark(args) -> int:
    from .hooks import run_hook

    cfg = load_config()
    conn = store.connect(_db_path(cfg))
    if not store.set_status(conn, args.uid, args.status, args.note or ""):
        print(f"no job with uid {args.uid}", file=sys.stderr)
        return 1
    row = store.get(conn, args.uid)
    print(f"{args.uid} -> {args.status}  ({row['company']} — {row['title']})")
    if args.no_hook:
        return 0
    ok, message = run_hook(args.status, cfg, store.row_to_dict(row))
    print(f"hook[{args.status}]: {'ok' if ok else 'FAILED'} — {message}")
    return 0 if ok else 2


def cmd_open(args) -> int:
    cfg = load_config()
    conn = store.connect(_db_path(cfg))
    for row in store.query(conn, limit=2000):
        if row["uid"] == args.uid:
            webbrowser.open(row["url"])
            print(row["url"])
            return 0
    print(f"no job with uid {args.uid}", file=sys.stderr)
    return 1


def cmd_discover(args) -> int:
    import json as _json

    from .resolve import probe

    existing = {(c["ats"], c["token"].lower()) for c in load_companies()}
    added, hits = [], []
    for target in args.targets:
        found = probe(target)
        if not found:
            if not args.json:
                print(f"  no ATS board found for {target!r}")
            continue
        for hit in found:
            hits.append(hit)
            key = (hit["ats"], hit["token"].lower())
            if args.json:
                print(_json.dumps({**hit, "target": target}))
            else:
                print(
                    f"  {hit['name']}: {hit['ats']}:{hit['token']} — {hit['jobs_count']} jobs "
                    f"(e.g. {hit['sample_title']!r} / {hit['sample_location']})"
                )
            if key in existing:
                continue
            existing.add(key)
            added.append({"name": hit["name"], "ats": hit["ats"], "token": hit["token"], "market": args.market or "", "tier": "", "auto": False, "enabled": True})
    if args.write and added:
        path = save_companies(load_companies() + added)
        print(f"watchlist updated: +{len(added)} → {path}")
    elif added and not args.json:
        print(f"({len(added)} new board(s); re-run with --write to add them to the watchlist)")
    return 0 if hits else 1


def cmd_export(args) -> int:
    cfg = load_config()
    conn = store.connect(_db_path(cfg))
    rows = store.query(
        conn,
        decision=args.decision,
        lane=args.lane,
        min_score=args.min_score,
        since_seconds=_since(args.since) or _since("14d"),
        status=args.status,
        limit=args.limit,
    )
    lines = [f"# jobradar export — {len(rows)} postings", ""]
    for row in rows:
        lines.append(
            f"- [ ] **{row['company']} — {row['title']}** · `{row['lane']}` · score {row['score']} · "
            f"{row['location'] or 'n/a'} · {row['url']} · seen {_date_only(row['first_seen'])} · `{row['uid']}`"
        )
    text = "\n".join(lines) + "\n"
    if args.out:
        pathlib.Path(args.out).write_text(text)
        print(f"wrote {args.out} ({len(rows)} rows)")
    else:
        print(text)
    return 0


def cmd_import(args) -> int:
    """Validate an external seed file (name/ats/token rows) and merge the working boards."""
    import yaml

    from .filters import Scorer
    from .net import Fetcher
    from .sources import FETCHERS

    seed = yaml.safe_load(pathlib.Path(args.seed).read_text()) or {}
    rows = seed.get("companies") if isinstance(seed, dict) else seed
    if not rows:
        print(f"no companies found in {args.seed}", file=sys.stderr)
        return 1

    cfg = load_config()
    scorer = Scorer(cfg["title_rules"], cfg["negative_rules"])
    existing = load_companies()
    known = {(c["ats"], c["token"].lower()) for c in existing}
    accepted, rejected = [], []
    fetcher = Fetcher(cfg["timeout"], cfg["user_agent"])
    try:
        for row in rows:
            ats, token = str(row.get("ats", "")).lower(), str(row.get("token", ""))
            name = row.get("name") or token
            if ats not in FETCHERS or not token:
                rejected.append((name, ats, token, "unknown ats"))
                continue
            if (ats, token.lower()) in known:
                rejected.append((name, ats, token, "already in watchlist"))
                continue
            try:
                jobs = FETCHERS[ats](fetcher, token, name)
            except Exception as exc:
                rejected.append((name, ats, token, f"{type(exc).__name__}: {exc}"[:70]))
                continue
            if not jobs:
                rejected.append((name, ats, token, "0 jobs"))
                continue
            lanes = Counter(scorer.lane(job) for job in jobs)
            market = lanes.most_common(1)[0][0]
            known.add((ats, token.lower()))
            accepted.append(
                {
                    "name": name,
                    "ats": ats,
                    "token": token,
                    "market": market,
                    "tier": row.get("tier", ""),
                    "auto": False,
                    "enabled": True,
                }
            )
            print(f"  {name}: {ats}:{token} — {len(jobs)} jobs, lane {market}")
    finally:
        fetcher.close()

    print(f"\naccepted {len(accepted)} / {len(rows)}")
    for name, ats, token, why in rejected[:40]:
        print(f"  skipped {name} ({ats}:{token}): {why}")
    if args.write and accepted:
        path = save_companies(existing + accepted)
        print(f"watchlist updated → {path} ({len(load_companies())} boards)")
    return 0


def cmd_rescore(args) -> int:
    from .scan import rescore as _rescore

    cfg = load_config()
    conn = store.connect(_db_path(cfg))
    result = _rescore(cfg, conn, prune=args.prune)
    line = f"rescored {result['rescored']} postings against the current rules (floor {result['floor']})"
    if args.prune:
        line += f"; pruned {result['pruned']} untouched below-floor rows"
    line += f"; {result['out_of_scope']} now out of scope"
    print(line)
    print(store.counts(conn)["by_decision"])
    return 0


def cmd_sources(args) -> int:
    cfg = load_config()
    conn = store.connect(_db_path(cfg))
    feeds = cfg.get("feeds") or []
    print(f"watchlist: {len(load_companies())} ATS boards  ({root() / str(cfg['companies_file'])})")
    print(f"feeds:     {len(feeds)} configured")
    rows = [dict(r) for r in conn.execute("SELECT source, jobs, last_error FROM fetches ORDER BY source")]
    for row in rows:
        row["last_fetch"] = _date_only(int(conn.execute("SELECT last_fetch FROM fetches WHERE source = ?", (row["source"],)).fetchone()[0]))
    print(
        _table(
            rows,
            [("source", 34), ("jobs", 6), ("last_fetch", 11), ("last_error", 50)],
            empty="(nothing fetched yet — run `jobradar scan`)",
        )
    )
    return 0


def cmd_tui(args) -> int:
    from .tui import RadarApp

    RadarApp(min_score=args.min_score, decision=args.decision).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobradar", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="fetch every source, score and store new postings")
    p.add_argument("--only", help="substring of a source id, e.g. ashby: or hn:")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--no-learn", action="store_true", help="do not auto-add ATS boards found in feed URLs")
    p.add_argument("--cache-minutes", type=int, default=None, help="skip sources fetched within N minutes")
    p.add_argument("--jobs", type=int, default=None, help="parallel fetches (default: scan_workers, 8)")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("list", help="list stored postings")
    p.add_argument("--status")
    p.add_argument("--decision", choices=["high", "review", "low", "out"])
    p.add_argument("--lane", choices=["eu-remote", "de", "ch", "fr", "nl", "au", "other"])
    p.add_argument("--min-score", type=int)
    p.add_argument("--since", help="e.g. 12h, 7d, 2w")
    p.add_argument("--search")
    p.add_argument("--reason", help="filter on a scoring reason tag, e.g. blockchain / mlops / k8s")
    p.add_argument("--region", choices=["eu", "emea", "au", "americas", "apac", "unknown"])
    p.add_argument("--language", choices=["en", "fr", "de", "unknown"])
    p.add_argument("--mode", choices=["remote", "hybrid", "onsite", "unknown"])
    p.add_argument("--country", choices=["fr", "de", "ch", "nl", "other", "unknown"])
    p.add_argument("--include-out", action="store_true", help="include out-of-scope rows (location/JD language/JD geography)")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="full record for one posting")
    p.add_argument("uid")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("mark", help="set status: shortlist|applied|rejected|ignored|new")
    p.add_argument("uid")
    p.add_argument("status", choices=list(store.STATUSES))
    p.add_argument("--note")
    p.add_argument("--no-hook", action="store_true", help="do not run the status hook")
    p.set_defaults(func=cmd_mark)

    p = sub.add_parser("open", help="open a posting in the browser")
    p.add_argument("uid")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("discover", help="resolve a company name or careers URL to an ATS board")
    p.add_argument("targets", nargs="+")
    p.add_argument("--write", action="store_true", help="append verified boards to the watchlist")
    p.add_argument("--market", help="market label to write with --write")
    p.add_argument("--json", action="store_true", help="emit one JSON object per verified board")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("export", help="markdown export for the pipeline tracker")
    p.add_argument("--since", default="14d")
    p.add_argument("--min-score", type=int)
    p.add_argument("--decision", choices=["high", "review", "low", "out"])
    p.add_argument("--lane")
    p.add_argument("--status")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--out")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("sources", help="show sources and their last fetch")
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("rescore", help="re-score stored postings after tuning the rules (no network)")
    p.add_argument("--prune", action="store_true", help="delete below-floor postings never acted on")
    p.set_defaults(func=cmd_rescore)

    p = sub.add_parser("import", help="validate a seed file of name/ats/token rows and merge the working ones")
    p.add_argument("seed")
    p.add_argument("--write", action="store_true")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("tui", help="open the terminal UI")
    p.add_argument("--min-score", type=int)
    p.add_argument("--decision", choices=["high", "review", "low", "out"])
    p.set_defaults(func=cmd_tui)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
