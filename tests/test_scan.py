"""Scan orchestration: ATS auto-learning, per-source failure isolation."""

from __future__ import annotations

import pathlib

import pytest
import yaml

from jobradar import scan as scan_mod
from jobradar import store
from jobradar.config import load_config
from jobradar.models import Job


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
