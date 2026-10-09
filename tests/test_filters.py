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
        ("Amsterdam, Netherlands", "nl"),
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


@pytest.mark.parametrize(
    "location",
    [
        "Paris, France",
        "Lyon",
        "Berlin, Germany",
        "München",
        "Zurich, Switzerland",
        "Lausanne, Vaud",
        "Amsterdam, Netherlands",
        "Rotterdam",
        "Berlin / Barcelona",  # a mixed posting keeps the target option
        "Hybrid (Paris)",
    ],
)
def test_physical_roles_in_a_target_country_are_admitted(scorer: Scorer, location: str) -> None:
    match = scorer.match(make("Senior ML Platform Engineer", location))
    assert match.decision != "out", match.reasons
    assert match.mode in ("onsite", "hybrid")


@pytest.mark.parametrize(
    "location",
    [
        "London, United Kingdom",
        "London",  # the reported case: a UK hybrid role
        "Remote - US",
        "Remote, United Kingdom",
        "NYC, ONSITE 3-4 days/wk",
        "ONSITE (SF)",
        "North America",
        "Gurugram",
        "Timișoara, TM, ro",
        "United States / Poland",
        "Sydney, Australia",
        "Madrid, MD, es",
        "Warsaw",
        "Stockholm",
        "APAC",
        "Headquarters",  # unplaceable: no country, no remote marker
        "Full-time",
    ],
)
def test_physical_roles_outside_the_target_countries_are_out(scorer: Scorer, location: str) -> None:
    match = scorer.match(make("Senior ML Platform Engineer", location))
    assert match.decision == "out"
    assert any(reason.endswith("-not-target") for reason in match.reasons), match.reasons


@pytest.mark.parametrize(
    "location",
    [
        "Remote",
        "Remote - Europe",
        "Remote - Worldwide",
        "REMOTE (EU)",
        "Home based - Worldwide",
        "EMEA",
        "Remote Germany",
        "Anywhere in the World",
        "Remote - USA / Remote - EMEA",  # the European option is real
    ],
)
def test_remote_roles_workable_from_france_stay_in_scope(scorer: Scorer, location: str) -> None:
    match = scorer.match(make("Senior ML Platform Engineer", location))
    assert match.mode == "remote"
    assert match.decision != "out", match.reasons


def test_a_remote_posting_with_no_geography_is_kept_but_flagged(scorer: Scorer) -> None:
    """A bare "Remote" is often a real EU-remote job; it is kept, tagged for audit."""
    match = scorer.match(make("Senior ML Platform Engineer", "Remote"))
    assert match.decision != "out"
    assert "-geo-unverified" in match.reasons
    # …but a posting that names a broad region is placed, not flagged
    assert "-geo-unverified" not in scorer.match(make("Senior ML Platform Engineer", "Remote - Europe")).reasons


@pytest.mark.parametrize(
    "location",
    ["Remote, United Kingdom", "Remote - US", "Remote (US)", "NYC or Remote", "Remote, Canada"],
)
def test_remote_roles_restricted_to_another_country_are_out(scorer: Scorer, location: str) -> None:
    match = scorer.match(make("Senior ML Platform Engineer", location))
    assert match.decision == "out"
    assert "-remote-not-target" in match.reasons


def test_target_country_is_recovered_from_the_jd_when_the_location_is_empty(scorer: Scorer) -> None:
    """Boards that publish no location at all still say "CHF" or "Switzerland" in the JD."""
    job = make("Senior Site Reliability Engineer", "")
    job.description = "Salary: CHF 140'000 - 180'000 per year Requirements: Swiss/EU/EFTA citizen"
    match = scorer.match(job)
    assert match.country == "ch"
    assert match.decision == "review"


def test_a_foreign_country_in_the_jd_is_not_a_location_signal(scorer: Scorer) -> None:
    """Only a *target* country is trusted in the JD body; everything else would be noise."""
    job = make("Senior Site Reliability Engineer", "")
    job.description = "We are a US company with offices in New York and a team in Europe."
    assert scorer.match(job).country == "unknown"


def test_the_eth_token_in_a_crypto_jd_does_not_move_a_job_to_switzerland(scorer: Scorer) -> None:
    """`\\beth\\b` (ETH Zürich) also matched the ETH token, which every Web3 JD mentions."""
    job = make("Blockchain Security Engineer", "")
    job.description = "Audit Solidity contracts, reason about ETH and EVM internals, ship tooling."
    match = scorer.match(job)
    assert match.country != "ch"
    assert match.decision == "out"


def test_mode_follows_the_location(scorer: Scorer) -> None:
    assert scorer.mode(make("Platform Engineer", "Remote - Europe")) == "remote"
    assert scorer.mode(make("Platform Engineer", "Berlin (Hybrid)")) == "hybrid"
    assert scorer.mode(make("Platform Engineer", "Berlin")) == "onsite"
    assert scorer.mode(make("Platform Engineer", "")) == "unknown"


def test_an_explicit_hybrid_location_beats_the_source_remote_flag(scorer: Scorer) -> None:
    """Ashby sets isRemote: true on postings whose location reads "The Hague - hybrid";
    the location is the truthful signal, so a hybrid role is not stored as remote."""
    job = make("Senior Infrastructure Engineer", "The Hague - hybrid")
    job.remote = True
    assert scorer.mode(job) == "hybrid"
    assert scorer.match(job).mode == "hybrid"


def test_hybrid_in_a_title_is_not_a_working_arrangement(scorer: Scorer) -> None:
    """'Hybrid Cloud Engineer' is a topic; a remote posting stays remote."""
    assert scorer.mode(make("Hybrid Cloud Engineer", "Remote - Europe")) == "remote"


def test_target_lanes_still_reach_high(scorer: Scorer) -> None:
    for location in ("Berlin, Germany", "Paris, France", "Zurich, Switzerland", "Remote - Europe"):
        assert scorer.match(make("Senior ML Platform Engineer", location)).decision == "high"


def test_physical_emea_roles_outside_the_target_countries_are_excluded(scorer: Scorer) -> None:
    """London/Tel Aviv are EMEA, but they are not places this search will work in person."""
    for location in ("London, United Kingdom", "Tel Aviv, Israel"):
        match = scorer.match(make("Senior ML Platform Engineer", location))
        assert match.decision == "out"
        assert "-onsite-not-target" in match.reasons


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


def test_off_lane_in_scope_role_is_capped_at_review(scorer: Scorer) -> None:
    """A remote-flagged posting whose location places it nowhere keeps lane "other"."""
    job = Job(
        company="Acme",
        title="ML Platform Engineer (Kubernetes)",
        url="https://example.invalid/1",
        source="test",
        location="Somewhere",
        remote=True,
    )
    match = scorer.match(job)
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
