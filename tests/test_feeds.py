"""Feed adapters: RSS/Atom, RemoteOK, Arbeitnow, HN Who-is-hiring."""

from __future__ import annotations

import pytest

from jobradar.sources import feeds


class RouteStub:
    def __init__(self, routes: dict[str, object] | None = None, text: str = "") -> None:
        self.routes = routes or {}
        self.body = text

    def json(self, url: str):
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        return None

    def text(self, url: str) -> str:
        return self.body


RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Remote DevOps Jobs</title>
  <item>
    <title>Acme: Senior Platform Engineer</title>
    <link>https://example.invalid/jobs/1</link>
    <pubDate>Mon, 22 Sep 2026 10:00:00 +0000</pubDate>
    <description>&lt;p&gt;Kubernetes &amp;amp; ML platform&lt;/p&gt;</description>
  </item>
  <item><title>No link here</title></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>EU Remote Jobs</title>
  <entry>
    <title>Platform Engineer at Beta Labs</title>
    <link href="https://example.invalid/atom/2"/>
    <updated>2026-09-20T08:00:00Z</updated>
    <summary>Observability and SRE</summary>
  </entry>
</feed>"""


def test_rss_extracts_title_link_date_and_strips_html() -> None:
    jobs = feeds.fetch_rss(RouteStub(text=RSS), "https://feed", "wwr")
    assert len(jobs) == 1  # the link-less entry is dropped
    job = jobs[0]
    assert job.company == "Acme"
    assert job.title == "Acme: Senior Platform Engineer"
    assert job.published == "2026-09-22"
    assert job.description == "Kubernetes & ML platform"
    assert job.source == "rss:wwr"


def test_atom_uses_href_and_guesses_company_from_title() -> None:
    job = feeds.fetch_rss(RouteStub(text=ATOM), "https://feed", "euremote")[0]
    assert job.url == "https://example.invalid/atom/2"
    assert job.company == "Beta Labs"
    assert job.published == "2026-09-20"


def test_remoteok_skips_the_legal_banner_entry() -> None:
    payload = [
        {"legal": "RemoteOK API terms"},
        {
            "id": "1",
            "position": "MLOps Engineer",
            "company": "Acme",
            "location": "Worldwide",
            "url": "https://remoteok.com/remote-jobs/1",
            "date": "2026-09-18T10:00:00+00:00",
            "description": "<p>k8s</p>",
            "tags": ["devops"],
        },
    ]
    jobs = feeds.fetch_remoteok(RouteStub({"remoteok": payload}), "remoteok", "remoteok")
    assert len(jobs) == 1
    assert jobs[0].title == "MLOps Engineer"
    assert jobs[0].published == "2026-09-18"
    assert jobs[0].remote is True


def test_arbeitnow_maps_remote_flag_and_epoch() -> None:
    payload = {
        "data": [
            {
                "title": "Platform Engineer",
                "company_name": "Beta",
                "location": "Berlin",
                "remote": True,
                "url": "https://arbeitnow.com/jobs/1",
                "created_at": 1780000000,
                "description": "<p>text</p>",
                "tags": ["kubernetes"],
                "job_types": ["permanent"],
            }
        ]
    }
    job = feeds.fetch_arbeitnow(RouteStub({"arbeitnow": payload}), "arbeitnow", "arbeitnow")[0]
    assert job.company == "Beta"
    assert job.remote is True
    assert job.published == "2026-05-28"


def test_whoishiring_splits_the_pipe_delimited_header() -> None:
    routes = {
        "search": {"hits": [{"objectID": "49522897", "title": "Ask HN: Who is hiring? (September 2026)"}]},
        "items/49522897": {
            "children": [
                {"id": 1, "created_at": "2026-09-01T15:00:00Z", "text": "Acme | ML Platform Engineer | Remote (EU) | Full-time"},
                {"id": 2, "created_at": "2026-09-01T16:00:00Z", "text": "just a sentence with no pipes"},
                {"id": 3, "created_at": "2026-09-01T17:00:00Z", "text": ""},
            ]
        },
    }
    jobs = feeds.fetch_hn_whoishiring(RouteStub(routes), "search", "hn")
    # Only the pipe-delimited posting is an offer; prose would become a row whose title
    # is the whole comment.
    assert len(jobs) == 1
    first = jobs[0]
    assert (first.company, first.title, first.location) == ("Acme", "ML Platform Engineer", "Remote (EU)")
    assert first.url == "https://news.ycombinator.com/item?id=1"
    assert first.remote is True


def test_whoishiring_skips_a_candidate_who_posted_their_own_profile() -> None:
    """HN 49686668: a "who wants to be hired?" profile pasted into the hiring thread."""
    routes = {
        "search": {"hits": [{"objectID": "49522897", "title": "Ask HN: Who is hiring? (September 2026)"}]},
        "items/49522897": {
            "children": [
                {
                    "id": 49686668,
                    "created_at": "2026-09-13T17:56:12Z",
                    "text": (
                        "Location: London, UK<br>Remote: Yes — UK remote or London hybrid/onsite<br>"
                        "Willing to relocate: No<br>Right to work: UK<br>Availability: Immediately"
                        "<p>Software Engineer with 4 years of experience…"
                    ),
                },
                {"id": 4, "created_at": "2026-09-13T18:00:00Z", "text": "Beta | SRE | Berlin"},
            ]
        },
    }
    jobs = feeds.fetch_hn_whoishiring(RouteStub(routes), "search", "hn")
    assert [job.company for job in jobs] == ["Beta"]


def test_whoishiring_bounds_a_long_role() -> None:
    routes = {
        "search": {"hits": [{"objectID": "1", "title": "Ask HN: Who is hiring? (September 2026)"}]},
        "items/1": {"children": [{"id": 9, "text": f"Acme | {'x' * 400} | Berlin"}]},
    }
    jobs = feeds.fetch_hn_whoishiring(RouteStub(routes), "search", "hn")
    assert len(jobs[0].title) == feeds.HIRING_POST_MAX


def test_whoishiring_raises_when_no_thread_is_present() -> None:
    with pytest.raises(ValueError):
        feeds.fetch_hn_whoishiring(RouteStub({"search": {"hits": []}}), "search", "hn")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://cryptojobslist.com/jobs/senior-devops-engineer-remote-at-chainlink-labs", "Chainlink Labs"),
        ("https://cryptojobslist.com/jobs/blockchain-engineer-at-balancer", "Balancer"),
        ("https://example.invalid/jobs/senior-platform-engineer", ""),
        ("", ""),
    ],
)
def test_company_from_url_recovers_the_employer_slug(url: str, expected: str) -> None:
    assert feeds.company_from_url(url) == expected


def test_rss_prefers_the_url_slug_over_the_feed_title() -> None:
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>Crypto Jobs List</title><item>
      <title>Senior DevOps Engineer (Remote)</title>
      <link>https://cryptojobslist.com/jobs/senior-devops-engineer-remote-at-kraken</link>
    </item></channel></rss>"""
    job = feeds.fetch_rss(RouteStub(text=xml), "https://feed", "cryptojobslist")[0]
    assert job.company == "Kraken"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Acme: Senior Platform Engineer", "Acme"),
        ("Platform Engineer at Beta Labs", "Beta Labs"),
        ("MLOps Engineer", "fallback"),
        # CryptoJobsList suffixes salary brackets; without stripping them the dash rule
        # swallowed the range and produced a company like "DeFi Engineer @ Di".
        ("DeFi Engineer @ Dialectic [CHF 110'000 - 150'000]", "Dialectic"),
        ("Senior DevOps Engineer (Remote) at Kraken", "Kraken"),
        ("Engineer @ Acme (m/f/d)", "Acme"),
    ],
)
def test_guess_company(title: str, expected: str) -> None:
    assert feeds.guess_company(title, "fallback") == expected


def test_company_from_url_handles_trailing_slugs() -> None:
    # strict form: employer is the whole tail
    assert feeds.company_from_url("https://cryptojobslist.com/jobs/devops-engineer-at-chainlink-labs") == "Chainlink Labs"
    # loose form: employer followed by salary segments
    assert feeds.company_from_url("https://cryptojobslist.com/jobs/defi-engineer-at-dialectic-chf-110-000") == "Dialectic"
