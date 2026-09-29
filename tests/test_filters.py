"""Filter behaviour: lanes, scope, negatives, ranking."""

from __future__ import annotations

import pytest

from jobradar.config import DEFAULTS
from jobradar.filters import Scorer
from jobradar.models import Job


@pytest.fixture
def scorer() -> Scorer:
    return Scorer(DEFAULTS["title_rules"], DEFAULTS["negative_rules"])


def make(title: str, location: str = "", company: str = "Acme", description: str = "") -> Job:
    return Job(
        company=company,
        title=title,
        url="https://example.invalid/1",
        source="test",
        location=location,
        description=description,
    )


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


def test_us_only_remote_is_out_of_scope_not_merely_demoted(scorer: Scorer) -> None:
    """A US-only role is out of scope, not a lower-priority opportunity."""
    outside = scorer.match(make("Senior Machine Learning Platform Engineer", "Remote - USA"))
    inside = scorer.match(make("Senior Machine Learning Platform Engineer", "Remote - Europe"))
    assert outside.decision == "out"
    assert outside.region == "americas"
    assert "-region-americas" in outside.reasons
    assert inside.decision == "high"


@pytest.mark.parametrize(
    ("location", "region"),
    [
        ("Berlin, Germany", "eu"),
        ("Paris, France", "eu"),
        ("London, United Kingdom", "eu"),
        ("Amsterdam, Netherlands", "eu"),
        ("Zurich, Switzerland", "eu"),
        ("Tel Aviv, Israel", "emea"),
        ("Dubai, UAE", "emea"),
        ("Sydney, Australia", "au"),
        ("Remote (Worldwide)", "eu"),
        ("Remote - EMEA", "eu"),
        ("San Francisco, CA, USA", "americas"),
        ("Toronto, Canada", "americas"),
        ("Bangalore, India", "apac"),
        ("Singapore", "apac"),
    ],
)
def test_region_classification_and_exclusion(scorer: Scorer, location: str, region: str) -> None:
    match = scorer.match(make("Senior ML Platform Engineer", location))
    assert match.region == region
    if region in ("americas", "apac"):
        assert match.decision == "out"  # hard exclusion
        assert f"-region-{region}" in match.reasons
    else:
        assert match.decision != "out"  # admitted, possibly capped to review


def test_target_lanes_still_reach_high(scorer: Scorer) -> None:
    for location in ("Berlin, Germany", "Paris, France", "Zurich, Switzerland", "Remote - Europe"):
        assert scorer.match(make("Senior ML Platform Engineer", location)).decision == "high"


def test_emea_onsite_outside_the_lanes_is_capped_not_excluded(scorer: Scorer) -> None:
    """London/Tel Aviv are in scope but are not target lanes: visible, not promoted."""
    for location in ("London, United Kingdom", "Tel Aviv, Israel"):
        match = scorer.match(make("Senior ML Platform Engineer", location))
        assert match.decision == "review"
        assert "-off-lane" in match.reasons


def test_out_of_scope_beats_even_a_low_score(scorer: Scorer) -> None:
    """The exclusion is a scope gate, not a score penalty."""
    weak = scorer.match(make("DevOps Engineer", "Austin, Texas, USA"))
    assert weak.score < 6
    assert weak.decision == "out"


def test_a_posting_offering_both_regions_stays_in_scope(scorer: Scorer) -> None:
    """"Remote - USA / Remote - EMEA" is applicable through the European option."""
    match = scorer.match(make("Senior Platform Engineer", "Remote - USA; Remote - EMEA"))
    assert match.region == "eu"
    assert match.decision != "out"


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
    """Off-lane but in scope (unclassified location) is capped, not excluded."""
    match = scorer.match(make("ML Platform Engineer (Kubernetes)", "Hybrid"))
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


GERMAN_JD = (
    "Wir suchen einen Senior Platform Engineer (m/w/d) für unser Team in Berlin. "
    "Deine Aufgaben: Du betreibst unsere Kubernetes-Umgebung und sorgst für Zuverlässigkeit. "
    "Dein Profil: mehrjährige Berufserfahrung mit Kubernetes, gute Kenntnisse in Python. "
    "Wir bieten flexible Arbeitszeiten und eine unbefristete Stelle in Vollzeit."
)
ENGLISH_JD = (
    "We are looking for a Senior Platform Engineer to join our team in Berlin. "
    "Your responsibilities: you will operate our Kubernetes platform and own reliability. "
    "Requirements: several years of experience with Kubernetes and strong Python skills. "
    "We offer flexible working hours and a permanent full-time contract."
)
FRENCH_JD = (
    "Nous recherchons un ingénieur plateforme senior pour rejoindre notre équipe à Paris. "
    "Vos missions : vous exploitez notre plateforme Kubernetes et garantissez la fiabilité. "
    "Profil recherché : plusieurs années d'expérience avec Kubernetes, compétences en Python. "
    "Nous proposons des horaires flexibles et un contrat en CDI à temps plein."
)


def test_german_language_jd_is_out_of_scope(scorer: Scorer) -> None:
    job = make("Senior Platform Engineer", "Berlin, Germany")
    job.description = GERMAN_JD
    match = scorer.match(job)
    assert match.language == "de"
    assert match.decision == "out"
    assert "-lang-de" in match.reasons


def test_english_jd_at_a_german_employer_stays_in_scope(scorer: Scorer) -> None:
    """The rule judges the JD language, never the country — the Berlin lane stays open."""
    job = make("Senior ML Platform Engineer", "Berlin, Germany", description=ENGLISH_JD)
    match = scorer.match(job)
    assert match.language == "en"
    assert match.decision == "high"
    assert match.region == "eu"


def test_french_jd_is_in_scope(scorer: Scorer) -> None:
    job = make("Ingénieur plateforme senior", "Paris, France")
    job.description = FRENCH_JD
    match = scorer.match(job)
    assert match.language == "fr"
    assert match.decision != "out"


def test_german_title_without_a_description_is_detected(scorer: Scorer) -> None:
    assert scorer.match(make("Softwareentwickler Kubernetes (m/w/d)", "Berlin")).language == "de"
    assert scorer.match(make("Site Reliability Engineer", "Berlin")).language == "unknown"


def test_language_detection_ignores_a_short_description(scorer: Scorer) -> None:
    """Below the body-length threshold only strong title markers are trusted."""
    job = make("Platform Engineer", "Remote")
    job.description = "Wir sind ein Team."
    assert scorer.match(job).language == "unknown"


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
