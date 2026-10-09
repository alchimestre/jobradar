"""Store: identity/dedupe, source merging, query filters, status transitions."""

from __future__ import annotations

import pytest

from jobradar import store
from jobradar.models import Job, Match


def pair(company="Acme", title="ML Platform Engineer", url="https://a/1", source="ashby:acme", score=9, decision="high", lane="de"):
    job = Job(company=company, title=title, url=url, source=source, location="Berlin", published="2026-09-01")
    return job, Match(score=score, decision=decision, lane=lane, reasons=["+ml-platform"])


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "state.db")


def test_same_posting_from_two_sources_collapses_to_one_row(conn) -> None:
    job, match = pair()
    assert store.upsert(conn, [(job, match)]) == (1, 0)

    same = Job(company="Acme", title="ML Platform Engineer", url="https://aggregator/xyz", source="rss:wwr", location="Berlin")
    new, seen = store.upsert(conn, [(same, Match(score=4, decision="review", lane="de"))])
    assert (new, seen) == (0, 1)

    row = store.query(conn)[0]
    assert row["sources"] == "ashby:acme,rss:wwr"
    assert row["score"] == 9  # best score wins, it is not downgraded by a weaker source


def test_one_requisition_seen_in_two_cities_stays_one_row(conn) -> None:
    """Identity is company+title, so per-board location formatting cannot fork a row."""
    job, match = pair()
    other = Job(company="Acme", title="ML Platform Engineer", url="https://a/2", source="ashby:acme", location="Remote, Europe")
    remote = Match(score=9, decision="high", lane="eu-remote", reasons=["+ml-platform"])
    assert store.upsert(conn, [(job, match), (other, remote)]) == (1, 1)

    row = store.query(conn)[0]
    assert row["location"] == "Berlin / Remote, Europe"
    assert row["sources"] == "ashby:acme"


def test_distinct_titles_are_distinct_rows(conn) -> None:
    store.upsert(conn, [pair(title="ML Platform Engineer"), pair(title="Site Reliability Engineer")])
    assert len(store.query(conn)) == 2


def test_a_scope_gate_change_reaches_an_existing_row(conn) -> None:
    """The gate is authoritative, not a score improvement: a posting that becomes out of
    scope flips to out even though its score did not rise. Without this, a rule change
    (JD language, region, target country) never reaches rows already in the store."""
    job, high = pair(score=9, decision="high")
    store.upsert(conn, [(job, high)])
    _, out = pair(score=9, decision="out")
    store.upsert(conn, [(job, out)])
    assert store.get(conn, job.uid)["decision"] == "out"


def test_a_weaker_source_still_does_not_downgrade_a_stored_decision(conn) -> None:
    """Keep-best survives the gate fix: a low-scoring source cannot demote a stored row."""
    job, high = pair(score=9, decision="high")
    store.upsert(conn, [(job, high)])
    _, low = pair(score=2, decision="low")
    store.upsert(conn, [(job, low)])
    assert store.get(conn, job.uid)["decision"] == "high"


def test_query_filters_compose(conn) -> None:
    store.upsert(
        conn,
        [
            pair(company="Acme", score=9, decision="high", lane="de"),
            pair(company="Beta", title="DevOps Engineer", score=2, decision="low", lane="eu-remote"),
        ],
    )
    assert [r["company"] for r in store.query(conn, min_score=5)] == ["Acme"]
    assert [r["company"] for r in store.query(conn, decision="low")] == ["Beta"]
    assert [r["company"] for r in store.query(conn, search="devops")] == ["Beta"]
    assert store.query(conn, status="applied") == []


def test_query_filters_by_scoring_reason(conn) -> None:
    """Web3 and ML lanes are both in scope, so they must be separable by tag."""
    ml, _ = pair(company="Acme", title="ML Platform Engineer")
    web3 = Job(company="Chainlink Labs", title="Blockchain Infrastructure Engineer", url="https://w/1", source="ashby:chainlink", location="Remote")
    store.upsert(conn, [(ml, Match(score=12, decision="high", lane="eu-remote", reasons=["+ml-platform"]))])
    store.upsert(conn, [(web3, Match(score=6, decision="high", lane="eu-remote", reasons=["+infra", "+blockchain"]))])

    assert [r["company"] for r in store.query(conn, reason="blockchain")] == ["Chainlink Labs"]
    assert [r["company"] for r in store.query(conn, reason="ml-platform")] == ["Acme"]
    assert store.query(conn, reason="nosuchtag") == []


def test_out_of_scope_rows_are_hidden_unless_asked_for(conn) -> None:
    """Region/language rejects are stored for auditing but never shown by default."""
    good, _ = pair(company="Acme", title="ML Platform Engineer")
    bad = Job(company="US Corp", title="Platform Engineer", url="https://us/1", source="x", location="Remote - USA")
    store.upsert(
        conn,
        [
            (good, Match(score=9, decision="high", lane="eu-remote", region="eu", language="en")),
            (bad, Match(score=6, decision="out", lane="other", region="americas", language="en", reasons=["-region-americas"])),
        ],
    )

    assert [r["company"] for r in store.query(conn)] == ["Acme"]
    assert [r["company"] for r in store.query(conn, include_out=True)] == ["Acme", "US Corp"]
    assert [r["company"] for r in store.query(conn, decision="out")] == ["US Corp"]
    assert [r["company"] for r in store.query(conn, region="eu")] == ["Acme"]

    result = store.counts(conn)
    assert result["out_of_scope"] == 1
    assert result["in_scope"] == 1
    assert result["by_region"] == {"eu": 1, "americas": 1}


def test_query_orders_eu_before_emea_before_unclassified(conn) -> None:
    for company, region in (("Unknown", "unknown"), ("Gulf", "emea"), ("Berlin", "eu")):
        job = Job(company=company, title="Platform Engineer", url=f"https://x/{company}", source="x")
        store.upsert(conn, [(job, Match(score=6, decision="review", lane="other", region=region))])
    assert [r["region"] for r in store.query(conn)] == ["eu", "emea", "unknown"]


def test_status_moves_and_is_validated(conn) -> None:
    job, match = pair()
    store.upsert(conn, [(job, match)])
    assert store.set_status(conn, job.uid, "applied", "issue #42") is True
    row = store.query(conn, status="applied")[0]
    assert row["notes"] == "issue #42"
    with pytest.raises(ValueError):
        store.set_status(conn, job.uid, "banana")


def test_counts_group_by_decision_and_status(conn) -> None:
    store.upsert(conn, [pair(company="Acme"), pair(company="Beta", title="DevOps Engineer", decision="low", score=2)])
    result = store.counts(conn)
    assert result["total"] == 2
    assert result["by_decision"] == {"high": 1, "low": 1}
    assert result["by_status"] == {"new": 2}
