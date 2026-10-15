"""Keyed and mixed-shape boards: Adzuna, web3.career, Lobsters who-is-hiring."""

from __future__ import annotations

import pytest

from jobradar.sources import keyed_boards as kb
from jobradar.sources.helpers import MissingCredential


class Stub:
    """Offline fetcher: routes match by substring, `text` serves every page fetch."""

    def __init__(self, routes: dict[str, object] | None = None, text: str = "") -> None:
        self.routes = routes or {}
        self.body = text
        self.urls: list[str] = []

    def json(self, url: str, headers: dict | None = None):
        self.urls.append(url)
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        raise AssertionError(f"no route for {url}")

    def text(self, url: str, headers: dict | None = None) -> str:
        self.urls.append(url)
        return self.body


def test_web3career_without_a_token_raises() -> None:
    with pytest.raises(MissingCredential):
        kb.fetch_web3career(Stub(), {})


WEB3_PAYLOAD = [
    "ok",
    "web3.career feed",
    [
        {
            "position": "Protocol Engineer",
            "company_name": "Chainlink",
            "location": "Remote",
            "url": "https://web3.career/protocol-engineer",
            "apply_url": "https://apply.example/protocol-engineer",
            "date": "2026-10-02T00:00:00Z",
            "description": "<p>Solidity and Rust</p>",
            "remote": True,
            "tags": ["rust", "solidity"],
        },
        {
            "title": "ZK Researcher",
            "organization": "ZkSync",
            "country": "DE",
            "apply_url": "https://apply.example/zk-researcher",
            "created_at": "2026-10-03",
            "remote": "yes",
        },
    ],
]


def test_web3career_locates_the_job_array_inside_the_mixed_root() -> None:
    stub = Stub({"api/v1": WEB3_PAYLOAD})
    jobs = kb.fetch_web3career(stub, {"token": "t", "limit": 500})

    assert [job.title for job in jobs] == ["Protocol Engineer", "ZK Researcher"]
    assert jobs[0].company == "Chainlink"
    assert jobs[0].url == "https://apply.example/protocol-engineer"  # attribution link
    assert jobs[0].published == "2026-10-02"
    assert jobs[0].description == "Solidity and Rust"
    assert jobs[0].extra["tags"] == ["rust", "solidity"]
    assert jobs[1].company == "ZkSync"
    assert jobs[1].location == "DE"
    assert jobs[1].remote is True
    assert "limit=100" in stub.urls[0]  # capped, not the 500 asked for


def test_web3career_accepts_filters_and_a_dict_payload() -> None:
    stub = Stub({"api/v1": {"jobs": [{"title": "Rust Engineer", "company": "Parity"}]}})
    jobs = kb.fetch_web3career(stub, {"token": "t", "tag": "rust", "remote": "true", "country": "de"})

    assert jobs[0].company == "Parity"
    assert "tag=rust" in stub.urls[0] and "remote=true" in stub.urls[0] and "country=de" in stub.urls[0]


def test_web3career_ignores_a_root_with_no_job_array() -> None:
    assert kb.fetch_web3career(Stub({"api/v1": ["ok", "nope"]}), {"token": "t"}) == []


def test_web3career_without_a_token_raises() -> None:
    with pytest.raises(MissingCredential):
        kb.fetch_web3career(Stub(), {})


LOBSTERS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Lobsters: job - Employment/Internship opportunities</title>
  <item>
    <title>Who's hiring? Q4 2026</title>
    <link>https://lobste.rs/s/bmcnwp/who_s_hiring_q4_2026</link>
    <guid>https://lobste.rs/s/bmcnwp</guid>
    <pubDate>Thu, 01 Oct 2026 05:03:52 -0500</pubDate>
  </item>
  <item>
    <title>Ask: how do you read code?</title>
    <link>https://lobste.rs/s/zzzzzz/how_do_you_read_code</link>
    <guid>https://lobste.rs/s/zzzzzz</guid>
  </item>
</channel></rss>"""

LOBSTERS_THREAD = {
    "title": "Who's hiring? Q4 2026",
    "comments": [
        {
            "depth": 0,
            "is_deleted": False,
            "is_moderated": False,
            "short_id_url": "https://lobste.rs/c/34snqk",
            "created_at": "2026-10-01T11:53:07.065-05:00",
            "comment_plain": (
                "**Company:** Censys (Censys.io)\r\n\r\n"
                "**Position(s):** Senior Platform Engineer\r\n\r\n"
                "**Location:** Remote, US\r\n\r\n"
                "**Description:** Censys scans the entire internet and provides intelligence."
            ),
        },
        {
            "depth": 1,
            "is_deleted": False,
            "is_moderated": False,
            "short_id_url": "https://lobste.rs/c/aaaaaa",
            "created_at": "2026-10-01T12:00:00.000-05:00",
            "comment_plain": "> Experience with object-oriented programming - we use Go.",
        },
        {
            "depth": 0,
            "is_deleted": True,
            "is_moderated": False,
            "short_id_url": "https://lobste.rs/c/deleted",
            "created_at": "2026-10-01T12:30:00.000-05:00",
            "comment_plain": "**Company:** Withdrawn Corp",
        },
        {
            "depth": 0,
            "is_deleted": False,
            "is_moderated": False,
            "short_id_url": "https://lobste.rs/c/bbb",
            "created_at": "2026-10-02T09:00:00.000-05:00",
            "comment_plain": "Kraken: Senior SRE, fully remote.\r\n\r\nWe run Kubernetes and Terraform.",
        },
    ],
}


def test_lobsters_takes_one_job_per_top_level_comment() -> None:
    stub = Stub({"bmcnwp.json": LOBSTERS_THREAD}, text=LOBSTERS_RSS)
    jobs = kb.fetch_lobsters(stub, {"name": "job"})

    assert [job.company for job in jobs] == ["Censys", "Kraken"]
    assert jobs[0].title == "Senior Platform Engineer"
    assert jobs[0].location == "Remote, US"
    assert jobs[0].url == "https://lobste.rs/c/34snqk"
    assert jobs[0].published == "2026-10-01"
    assert jobs[0].source == "lobsters:job"
    assert jobs[0].remote is True
    assert jobs[0].extra["thread"] == "Who's hiring? Q4 2026"
    # Free-text post: the first line is the title, `guess_company` recovers the employer.
    assert jobs[1].title == "Kraken: Senior SRE, fully remote."
    assert jobs[1].url == "https://lobste.rs/c/bbb"
    assert "Kubernetes and Terraform" in jobs[1].description
    assert stub.urls[1] == "https://lobste.rs/s/bmcnwp.json"


def test_lobsters_honours_max_items() -> None:
    stub = Stub({"bmcnwp.json": LOBSTERS_THREAD}, text=LOBSTERS_RSS)
    assert len(kb.fetch_lobsters(stub, {"max_items": 1})) == 1


def test_lobsters_raises_when_the_feed_has_no_hiring_thread() -> None:
    feed = LOBSTERS_RSS.replace("Who's hiring? Q4 2026", "A blog post")
    with pytest.raises(ValueError):
        kb.fetch_lobsters(Stub(text=feed), {})


def test_fetchers_are_registered_under_their_spec_names() -> None:
    assert set(kb.FETCHERS) == {"web3career", "lobsters"}
