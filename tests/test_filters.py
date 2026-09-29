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
