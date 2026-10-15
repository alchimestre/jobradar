"""Scan orchestration: ATS auto-learning, per-source failure isolation."""

from __future__ import annotations

import pathlib

import pytest
import yaml

from jobradar import scan as scan_mod
from jobradar import store
from jobradar.config import load_config
from jobradar.models import Job, Match


class StubFetcher:
    def __init__(self, routes: dict[str, object] | None = None) -> None:
        self.routes = routes or {}

    def json(self, url: str):
        return self.routes[url]

    def text(self, url: str) -> str:
        return ""

    def close(self) -> None:
        return None


def write_watchlist(home: pathlib.Path, entries: list[dict]) -> None:
    (home / "companies.yaml").write_text(yaml.safe_dump({"companies": entries}))
    (home / "config.yaml").write_text("feeds: []\n")


def test_scan_learns_a_new_ats_board_from_a_feed_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "feeds:\n  - name: fake\n    type: arbeitnow\n    url: http://feed.invalid\n"
    )
    (tmp_path / "companies.yaml").write_text("companies: []\n")
    payload = {
        "data": [
            {
                "title": "ML Platform Engineer",
                "company_name": "NewCo",
                "location": "Berlin",
                "remote": False,
                "url": "https://jobs.ashbyhq.com/newco/123",
                "created_at": 1780000000,
                "description": "kubernetes ml platform",
                "tags": [],
                "job_types": [],
            }
        ]
    }
    monkeypatch.setattr(scan_mod, "Fetcher", lambda *a, **k: StubFetcher({"http://feed.invalid": payload}))
    cfg = load_config()
    conn = store.connect(tmp_path / "state.db")

    report = scan_mod.scan(cfg, conn)

    assert report.new == 1
    assert [(row["ats"], row["token"]) for row in report.learned] == [("ashby", "newco")]
    written = yaml.safe_load((tmp_path / "companies.yaml").read_text())["companies"]
    assert written[0]["token"] == "newco"
    assert written[0]["auto"] is True


def test_one_broken_board_does_not_stop_the_scan(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    write_watchlist(
        tmp_path,
        [
            {"name": "Good", "ats": "ashby", "token": "good"},
            {"name": "Bad", "ats": "greenhouse", "token": "bad"},
        ],
    )

    def explode(fetcher, token, company):
        raise RuntimeError("HTTP 404")

    monkeypatch.setattr(
        scan_mod,
        "FETCHERS",
        {"ashby": lambda f, t, n: [Job(company=n, title="ML Platform Engineer", url="https://a/1", source=f"ashby:{t}", location="Berlin")], "greenhouse": explode},
    )
    cfg = load_config()
    conn = store.connect(tmp_path / "state.db")

    report = scan_mod.scan(cfg, conn)

    assert [row["source"] for row in report.errors] == ["greenhouse:bad"]
    assert report.new == 1
    assert "1 error" in report.summary()


def test_disabled_board_is_neither_fetched_nor_relearned(tmp_path, monkeypatch) -> None:
    """`enabled: false` means mute: it must not be polled, nor re-added by auto-learning."""
    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    write_watchlist(tmp_path, [{"name": "Muted", "ats": "recruitee", "token": "muted", "enabled": False}])
    (tmp_path / "config.yaml").write_text(
        "feeds:\n  - name: fake\n    type: arbeitnow\n    url: http://feed.invalid\n"
    )
    payload = {
        "data": [
            {
                "title": "ML Platform Engineer",
                "company_name": "Muted",
                "location": "Berlin",
                "remote": False,
                "url": "https://muted.recruitee.com/o/platform-engineer",
                "created_at": 1780000000,
                "description": "",
                "tags": [],
                "job_types": [],
            }
        ]
    }
    monkeypatch.setattr(scan_mod, "Fetcher", lambda *a, **k: StubFetcher({"http://feed.invalid": payload}))
    polled = []
    monkeypatch.setattr(scan_mod, "FETCHERS", {"recruitee": lambda f, t, n: polled.append(t) or []})
    cfg = load_config()
    conn = store.connect(tmp_path / "state.db")

    report = scan_mod.scan(cfg, conn)

    assert polled == []
    assert report.learned == []
    written = yaml.safe_load((tmp_path / "companies.yaml").read_text())["companies"]
    assert written[0]["enabled"] is False


def test_learning_a_board_keeps_disabled_rows_and_stays_quiet(tmp_path, monkeypatch) -> None:
    """Auto-learning rewrites the watchlist; muted entries must survive that rewrite."""
    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    write_watchlist(tmp_path, [{"name": "Muted", "ats": "recruitee", "token": "muted", "enabled": False}])
    (tmp_path / "config.yaml").write_text(
        "feeds:\n  - name: fake\n    type: arbeitnow\n    url: http://feed.invalid\n"
    )
    payload = {
        "data": [
            {
                "title": "ML Platform Engineer",
                "company_name": "NewCo",
                "location": "Berlin",
                "remote": False,
                "url": "https://jobs.ashbyhq.com/newco/123",
                "created_at": 1780000000,
                "description": "",
                "tags": [],
                "job_types": [],
            }
        ]
    }
    monkeypatch.setattr(scan_mod, "Fetcher", lambda *a, **k: StubFetcher({"http://feed.invalid": payload}))
    monkeypatch.setattr(scan_mod, "FETCHERS", {})
    cfg = load_config()
    conn = store.connect(tmp_path / "state.db")

    scan_mod.scan(cfg, conn)

    written = {c["token"]: c for c in yaml.safe_load((tmp_path / "companies.yaml").read_text())["companies"]}
    assert written["muted"]["enabled"] is False
    assert written["newco"]["auto"] is True


def test_rescore_lowers_scores_that_upsert_would_have_kept(tmp_path, monkeypatch) -> None:
    """Upsert only keeps the best score, so rule tightening needs an explicit re-score."""
    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    write_watchlist(tmp_path, [])
    cfg = load_config()
    conn = store.connect(tmp_path / "state.db")
    job = Job(company="Acme", title="DevOps Engineer", url="https://a/1", source="x", location="Remote")
    store.upsert(conn, [(job, Match(score=99, decision="high", lane="eu-remote", reasons=["+stale"]))])

    result = scan_mod.rescore(cfg, conn)

    row = store.query(conn)[0]
    assert result["rescored"] == 1
    assert row["score"] < 99
    assert row["decision"] == "low"
    assert "+stale" not in row["reasons"]


def test_rescore_prune_spares_rows_already_acted_on(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    write_watchlist(tmp_path, [])
    cfg = load_config()
    conn = store.connect(tmp_path / "state.db")
    untouched = Job(company="Noise", title="Growth Manager", url="https://a/1", source="x", location="Remote")
    applied = Job(company="Acme", title="DevOps Engineer", url="https://a/2", source="x", location="Remote")
    store.upsert(conn, [(untouched, Match(score=1, decision="low", lane="eu-remote")), (applied, Match(score=1, decision="low", lane="eu-remote"))])
    store.set_status(conn, applied.uid, "applied")

    result = scan_mod.rescore(cfg, conn, prune=True)

    remaining = {row["company"] for row in store.query(conn)}
    assert result["pruned"] == 1
    assert remaining == {"Acme"}


def test_rescore_reclassifies_region_and_language(tmp_path, monkeypatch) -> None:
    """Rule changes must reach rows stored before them (the DB is not immutable)."""
    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    write_watchlist(tmp_path, [])
    cfg = load_config()
    conn = store.connect(tmp_path / "state.db")
    job = Job(company="US Corp", title="Senior Platform Engineer", url="https://us/1", source="x", location="San Francisco, CA, USA")
    store.upsert(conn, [(job, Match(score=9, decision="high", lane="other"))])

    result = scan_mod.rescore(cfg, conn)

    row = store.query(conn, decision="out")[0]
    assert row["decision"] == "out"
    assert row["region"] == "americas"
    assert result["out_of_scope"] == 1


def test_scan_skips_sources_fetched_within_the_cache_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    write_watchlist(tmp_path, [{"name": "Good", "ats": "ashby", "token": "good"}])
    calls = []

    def counted(fetcher, token, company):
        calls.append(token)
        return []

    monkeypatch.setattr(scan_mod, "FETCHERS", {"ashby": counted})
    cfg = load_config()
    conn = store.connect(tmp_path / "state.db")

    scan_mod.scan(cfg, conn, cache_minutes=60)
    second = scan_mod.scan(cfg, conn, cache_minutes=60)

    assert calls == ["good"]
    assert second.sources[0]["cached"] is True


def arbeitnow_payload(title: str, company: str, url: str) -> dict:
    return {
        "data": [
            {
                "title": title,
                "company_name": company,
                "location": "Berlin",
                "remote": False,
                "url": url,
                "created_at": 1780000000,
                "description": (
                    "We are looking for a platform engineer to run our Kubernetes and Terraform "
                    "estate, owning reliability for the ML platform across Europe."
                ),
                "tags": [],
                "job_types": [],
            }
        ]
    }


def test_parallel_fetch_keeps_the_serial_result_and_source_order(tmp_path, monkeypatch) -> None:
    """Concurrency is a speed change only: same rows, same order, same decisions."""
    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    feeds = [
        {"name": f"feed{n}", "type": "arbeitnow", "url": f"http://feed.invalid/{n}"} for n in range(3)
    ]
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"feeds": feeds}))
    (tmp_path / "companies.yaml").write_text("companies: []\n")
    routes = {
        f"http://feed.invalid/{n}": arbeitnow_payload(
            f"ML Platform Engineer {n}", f"Co{n}", f"https://example.test/{n}"
        )
        for n in range(3)
    }
    monkeypatch.setattr(scan_mod, "Fetcher", lambda *a, **k: StubFetcher(routes))
    cfg = load_config()

    serial = scan_mod.scan(cfg, store.connect(tmp_path / "serial.db"), workers=1)
    parallel = scan_mod.scan(cfg, store.connect(tmp_path / "parallel.db"), workers=4)

    assert [row["source"] for row in serial.sources] == [row["source"] for row in parallel.sources]
    assert serial.sources == parallel.sources
    assert (serial.new, serial.scored) == (parallel.new, parallel.scored) == (3, 3)


def test_a_muted_feed_is_never_fetched(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "feeds:\n"
        "  - name: muted\n    type: arbeitnow\n    url: http://feed.invalid/m\n    enabled: false\n"
        "  - name: live\n    type: arbeitnow\n    url: http://feed.invalid/l\n"
    )
    (tmp_path / "companies.yaml").write_text("companies: []\n")
    routes = {
        "http://feed.invalid/l": arbeitnow_payload("SRE", "Co", "https://example.test/l"),
        "http://feed.invalid/m": arbeitnow_payload("SRE", "Muted", "https://example.test/m"),
    }
    monkeypatch.setattr(scan_mod, "Fetcher", lambda *a, **k: StubFetcher(routes))
    cfg = load_config()
    conn = store.connect(tmp_path / "state.db")

    report = scan_mod.scan(cfg, conn)

    assert [row["source"] for row in report.sources] == ["arbeitnow:live"]
    assert report.feeds == 1
    assert {row["company"] for row in store.query(conn)} == {"Co"}
