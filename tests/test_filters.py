"""Filter behaviour: lanes, scope, negatives, ranking."""

from __future__ import annotations

import pytest

from jobradar.config import DEFAULTS
from jobradar.filters import Scorer
from jobradar.models import Job


@pytest.fixture
def scorer() -> Scorer:
    return Scorer(DEFAULTS["title_rules"], DEFAULTS["negative_rules"])


def make(title: str, location: str = "", company: str = "Acme") -> Job:
    return Job(company=company, title=title, url="https://example.invalid/1", source="test", location=location)


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Remote", "eu-remote"),
        ("Remote (Europe)", "eu-remote"),
        ("Berlin, Germany", "de"),
        ("Munich", "de"),
        ("Zurich, Switzerland", "ch"),
        ("Paris, France", "fr"),
        ("Sydney, Australia", "au"),
        ("Singapore", "other"),
    ],
)
def test_lane_detection(scorer: Scorer, location: str, expected: str) -> None:
    assert scorer.match(make("Platform Engineer", location)).lane == expected


def test_berlin_remote_stays_in_the_german_lane(scorer: Scorer) -> None:
    """A Berlin posting that allows remote work is still the Berlin lane, not EU-remote."""
    assert scorer.match(make("Platform Engineer", "Berlin, Germany (Remote)")).lane == "de"


def test_remote_scope_outside_europe_is_penalised(scorer: Scorer) -> None:
    outside = scorer.match(make("Platform Engineer", "Remote, United States"))
    inside = scorer.match(make("Platform Engineer", "Remote, Europe"))
    assert outside.scope == "restricted"
    assert inside.scope == "ok"
    assert outside.score < inside.score
    assert "-scope-outside-eu" in outside.reasons


def test_us_only_remote_cannot_reach_high_even_with_a_strong_title(scorer: Scorer) -> None:
    """Coinbase-style "Remote - USA" postings must not outrank genuine EU-remote roles."""
    outside = scorer.match(make("Senior Machine Learning Platform Engineer", "Remote - USA"))
    inside = scorer.match(make("Senior Machine Learning Platform Engineer", "Remote - Europe"))
    assert outside.decision == "review"
    assert inside.decision == "high"
    assert "-out-of-scope" in outside.reasons


def test_leadership_title_is_not_admitted_on_a_platform_keyword(scorer: Scorer) -> None:
    assert scorer.match(make("Engineering Manager, Platform", "Berlin")).decision == "low"


def test_mlops_outranks_generic_devops(scorer: Scorer) -> None:
    mlops = scorer.match(make("MLOps Engineer", "Remote"))
    devops = scorer.match(make("DevOps Engineer", "Remote"))
    assert mlops.score > devops.score
    assert mlops.decision == "high"
    assert devops.decision == "low"


def test_ml_platform_role_with_k8s_is_high(scorer: Scorer) -> None:
    match = scorer.match(make("Senior ML Platform Engineer (Kubernetes)", "Berlin, Germany"))
    assert match.decision == "high"
    assert match.lane == "de"
    assert {"+ml-platform", "+k8s"} <= set(match.reasons)


def test_strong_title_outside_every_target_lane_is_capped_at_review(scorer: Scorer) -> None:
    match = scorer.match(make("ML Platform Engineer (Kubernetes)", "San Francisco, United States"))
    assert match.lane == "other"
    assert match.decision == "review"
    assert "-off-lane" in match.reasons


def test_web3_infrastructure_is_admitted_but_ranks_below_ml(scorer: Scorer) -> None:
    """Web3 is in scope; ML/MLOps stays the preference without gating Web3 out."""
    web3 = scorer.match(make("Blockchain Infrastructure Engineer", "Remote"))
    web3_devops = scorer.match(make("DevOps Engineer, Web3", "Remote"))
    mlops = scorer.match(make("MLOps Engineer", "Remote"))

    assert web3.decision == "high" and "+blockchain" in web3.reasons
    assert web3_devops.decision == "review"  # reachable, not buried under the floor
    assert mlops.score > scorer.match(make("Blockchain Engineer", "Remote")).score


def test_web3_marketing_and_sales_stay_rejected(scorer: Scorer) -> None:
    """Widening scope admits infrastructure, not the crypto-adjacent GTM funnel."""
    assert scorer.match(make("Web3 Growth Manager", "Remote")).decision == "low"
    assert scorer.match(make("Crypto Sales Executive", "Remote")).decision == "low"


def test_internship_is_filtered_out(scorer: Scorer) -> None:
    assert scorer.match(make("Platform Engineering Intern", "Berlin")).decision == "low"


def test_staff_level_stretch_is_soft_penalised_not_admitted(scorer: Scorer) -> None:
    match = scorer.match(make("Staff Site Reliability Engineer", "Berlin, Germany"))
    assert match.decision == "low"
    assert "-seniority-stretch" in match.reasons


def test_pure_data_pipeline_role_is_demoted(scorer: Scorer) -> None:
    pipeline = scorer.match(make("Senior Data Engineer", "Berlin"))
    platform = scorer.match(make("Senior Platform Engineer", "Berlin"))
    assert pipeline.score < platform.score
