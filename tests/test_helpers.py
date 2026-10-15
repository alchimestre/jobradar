"""Shared source helpers: HTML text, credentials, bounds, JSON-LD extraction, enrichment."""

from __future__ import annotations

import json

import pytest

from jobradar.models import Job
from jobradar.sources import helpers


def job(description: str = "", url: str = "https://example.test/job") -> Job:
    return Job(company="Acme", title="Platform Engineer", url=url, source="test:acme", description=description)


class StubFetcher:
    """Serves one page and records the URLs actually requested."""

    def __init__(self, body: str = "") -> None:
        self.body = body
        self.calls: list[str] = []

    def text(self, url: str, headers=None) -> str:
        self.calls.append(url)
        return self.body


def test_strip_html_unescapes_before_stripping_tags() -> None:
    """Feeds double-escape their HTML; the other order leaves the markup in the text."""
    assert helpers.strip_html("&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;") == "Hello world"


def test_strip_html_collapses_whitespace_and_empty_input() -> None:
    assert helpers.strip_html("<p>a\n\n   b</p>") == "a b"
    assert helpers.strip_html(None) == ""


def test_credential_raises_when_the_key_is_absent() -> None:
    with pytest.raises(helpers.MissingCredential) as excinfo:
        helpers.credential({}, "app_key", "JOBRADAR_ADZUNA_APP_KEY")
    assert "JOBRADAR_ADZUNA_APP_KEY" in str(excinfo.value)


def test_credential_reads_the_spec() -> None:
    assert helpers.credential({"app_key": "abc"}, "app_key", "X") == "abc"


def test_headers_selects_the_browser_user_agent() -> None:
    assert helpers.headers({}) is None
    assert helpers.headers({"user_agent": "browser"}) == {"User-Agent": helpers.BROWSER_UA}
    assert helpers.headers({"user_agent": "custom/1"}) == {"User-Agent": "custom/1"}
    assert helpers.headers({"user_agent": "browser", "headers": {"Origin": "https://x"}}) == {
        "User-Agent": helpers.BROWSER_UA,
        "Origin": "https://x",
    }


def test_bound_falls_back_on_a_missing_or_invalid_value() -> None:
    assert helpers.bound({"max_pages": "4"}, "max_pages", 2) == 4
    assert helpers.bound({"max_pages": "many"}, "max_pages", 2) == 2
    assert helpers.bound({}, "max_pages", 2) == 2


def test_job_postings_reads_a_plain_ld_json_block() -> None:
    page = '<script type="application/ld+json">{"@type":"JobPosting","title":"SRE"}</script>'
    assert helpers.job_postings(page) == [{"@type": "JobPosting", "title": "SRE"}]


def test_job_postings_reads_an_escaped_ld_json_type() -> None:
    """HelloWork escapes the `+` as `&#x2B;`, which a plain search misses entirely."""
    page = '<script type="application/ld&#x2B;json">{"@type":"JobPosting","title":"SRE"}</script>'
    assert [posting["title"] for posting in helpers.job_postings(page)] == ["SRE"]


def test_job_postings_walks_graph_and_ignores_broken_json() -> None:
    page = (
        '<script type="application/ld+json">{"@graph":[{"@type":"WebSite"},'
        '{"@type":"JobPosting","title":"SRE"}]}</script>'
        '<script type="application/ld+json">{not json</script>'
    )
    assert [posting["title"] for posting in helpers.job_postings(page)] == ["SRE"]


def test_description_from_page_prefers_the_json_ld_body() -> None:
    body = (
        "&lt;p&gt;Kubernetes and Terraform across a large estate, running the platform team's own "
        "tooling for forty engineering squads in Paris and Berlin.&lt;/p&gt;"
    )
    page = (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "JobPosting", "description": body})
        + "</script><body>navigation noise</body>"
    )
    assert helpers.description_from_page(page).startswith("Kubernetes and Terraform")


def test_description_from_page_falls_back_to_the_page_text() -> None:
    assert helpers.description_from_page("<p>Plain <b>page</b></p>") == "Plain page"


def test_enrich_replaces_a_teaser_with_the_page_text() -> None:
    posting = job("Acme is hiring a Platform Engineer.")
    fetcher = StubFetcher('<script type="application/ld+json">{"@type":"JobPosting","description":"'
                          + "Nous recherchons un ingénieur plateforme pour notre équipe infrastructure, "
                          + 'sur Kubernetes et Terraform."}</script>')
    out = helpers.enrich(fetcher, [posting], {"enrich_limit": 5})
    assert "ingénieur plateforme" in out[0].description
    assert fetcher.calls == ["https://example.test/job"]


def test_enrich_spends_its_budget_only_on_short_descriptions() -> None:
    long_one = job("x" * 200, url="https://example.test/a")
    short_one = job("teaser", url="https://example.test/b")
    fetcher = StubFetcher("<p>" + "y" * 300 + "</p>")
    helpers.enrich(fetcher, [long_one, short_one], {"enrich_limit": 5})
    assert fetcher.calls == ["https://example.test/b"]


def test_enrich_stops_at_the_budget() -> None:
    jobs = [job("teaser", url=f"https://example.test/{n}") for n in range(5)]
    fetcher = StubFetcher("<p>" + "y" * 300 + "</p>")
    helpers.enrich(fetcher, jobs, {"enrich_limit": 2})
    # Budget, not order: the two page fetches happen together.
    assert sorted(fetcher.calls) == ["https://example.test/0", "https://example.test/1"]


def test_enrich_is_off_without_a_budget() -> None:
    fetcher = StubFetcher("<p>" + "y" * 300 + "</p>")
    helpers.enrich(fetcher, [job("teaser")], {})
    assert fetcher.calls == []


def test_enrich_keeps_the_row_when_the_page_fails() -> None:
    class Broken:
        def text(self, url, headers=None):
            raise RuntimeError("503")

    posting = job("teaser")
    helpers.enrich(Broken(), [posting], {"enrich_limit": 1})
    assert posting.description == "teaser"


def test_company_from_url_recovers_the_employer_slug() -> None:
    assert helpers.company_from_url("https://cryptojobslist.com/jobs/x-at-chainlink") == "Chainlink"
    assert helpers.company_from_url("https://cryptojobslist.com/jobs/x-at-dialectic-chf-110-000") == "Dialectic"
    assert helpers.company_from_url("https://example.test/jobs/123") == ""


def test_guess_company_strips_a_trailing_bracket_but_not_a_remote_marker() -> None:
    assert helpers.guess_company("SRE @ Dialectic [CHF 110'000 - 150'000]", "feed") == "Dialectic"
    assert helpers.guess_company("Platform Engineer (Remote) at Kraken", "feed") == "Kraken"
    assert helpers.guess_company("Platform Engineer", "feed") == "feed"
