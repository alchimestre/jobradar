"""URL→ATS resolution and token guessing."""

from __future__ import annotations

import pytest

from jobradar.resolve import board_name, learn_from_url, variants


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://jobs.ashbyhq.com/vClusterLabs/19a7f22e-ee7d", ("ashby", "vClusterLabs")),
        ("https://jobs.ashbyhq.com/cohere/69a9ab2e", ("ashby", "cohere")),
        ("https://job-boards.greenhouse.io/gitlab/jobs/8556658002", ("greenhouse", "gitlab")),
        ("https://boards.greenhouse.io/stripe/jobs/1", ("greenhouse", "stripe")),
        ("https://jobs.lever.co/acme/123", ("lever", "acme")),
        ("https://apply.workable.com/huggingface/j/ABC123/", ("workable", "huggingface")),
        ("https://careers.smartrecruiters.com/BoschGroup/744000152406179", ("smartrecruiters", "BoschGroup")),
        ("https://personio.jobs.personio.de/job/1834171", ("personio", "personio")),
        ("https://acme.recruitee.com/o/platform-engineer", ("recruitee", "acme")),
        ("https://apply.workable.com/acme/j/ABC123", ("workable", "acme")),
        # Workable's own job path must not be learned as an employer called "j".
        ("https://apply.workable.com/j/ABC123", None),
        ("https://ag.wd3.myworkdayjobs.com/Airbus/job/Senior-SRE_JR123", ("workday", "ag.wd3/Airbus")),
        (
            "https://ag.wd3.myworkdayjobs.com/en-US/Airbus/job/Senior-SRE_JR123",
            ("workday", "ag.wd3/Airbus"),
        ),
        ("https://ag.wd3.myworkdayjobs.com/wday/cxs/ag/Airbus/jobs", ("workday", "ag.wd3/Airbus")),
        ("https://europe-wavestone.career.softgarden.de/jobs/123", ("softgarden", "europe-wavestone")),
    ],
)
def test_known_ats_urls_resolve(url: str, expected: tuple[str, str]) -> None:
    assert learn_from_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/jobs/1",
        "https://news.ycombinator.com/item?id=49522897",
        "https://www.linkedin.com/jobs/view/4460371549/",
        "",
    ],
)
def test_non_ats_urls_return_nothing(url: str) -> None:
    assert learn_from_url(url) is None


@pytest.mark.parametrize(
    ("company", "token", "expected"),
    [
        ("vCluster Labs", "vClusterLabs", "vCluster Labs"),
        ("NewCo", "newco", "NewCo"),
        ("Hetzner", "xneelo", "xneelo"),      # subsidiary board reached from a group feed
        ("", "gitlab", "gitlab"),
    ],
)
def test_board_name_prefers_the_employer_only_when_it_matches_the_slug(
    company: str, token: str, expected: str
) -> None:
    assert board_name(company, token) == expected


def test_variants_cover_squashed_and_dashed_forms() -> None:
    mistral = variants("Mistral AI")
    assert {"mistralai", "mistral-ai", "MistralAI", "Mistral-AI", "mistral"} <= set(mistral)
    black_forest = variants("Black Forest Labs")
    assert {"blackforestlabs", "black-forest-labs", "BlackForestLabs"} <= set(black_forest)
    assert "1komma5grad" in {v.lower() for v in variants("1Komma5Grad GmbH")}
    assert variants("X") == []  # too short to guess
    assert len(variants("Some Very Long Company Name Here")) <= 6
