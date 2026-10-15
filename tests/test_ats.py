"""ATS adapters: field mapping and failure modes, against captured payload shapes."""

from __future__ import annotations

import pytest

from jobradar.sources import ats


class StubFetcher:
    """Stands in for jobradar.net.Fetcher; serves one captured payload."""

    def __init__(self, payload=None, text: str = "") -> None:
        self.payload = payload
        self.body = text

    def json(self, url: str):
        return self.payload

    def text(self, url: str) -> str:
        return self.body


ASHBY = {
    "jobs": [
        {
            "id": "abc",
            "title": "AI Infrastructure Engineer",
            "location": "Remote",
            "jobUrl": "https://jobs.ashbyhq.com/vClusterLabs/19a7f22e",
            "publishedAt": "2026-04-27T21:05:49.812+00:00",
            "isRemote": True,
            "isListed": True,
            "descriptionPlain": "Kubernetes, ML platform",
        },
        {"id": "hidden", "title": "Unlisted Role", "jobUrl": "", "isListed": False},
    ]
}

GREENHOUSE = {
    "jobs": [
        {
            "id": 8556658002,
            "title": "AI Engineer",
            "absolute_url": "https://job-boards.greenhouse.io/gitlab/jobs/8556658002",
            "location": {"name": "Remote, Bangalore"},
            "first_published": "2026-05-22T09:16:29-04:00",
            "updated_at": "2026-09-14T16:01:39-04:00",
            "content": (
                "&lt;div&gt;&lt;p&gt;&lt;strong&gt;About&lt;/strong&gt; GitLab&lt;/p&gt;"
                "&lt;p&gt;Kubernetes and Terraform at scale.&lt;/p&gt;&lt;/div&gt;"
            ),
        }
    ]
}

LEVER = [
    {
        "text": "Senior Platform Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/123",
        "categories": {"location": "Berlin", "commitment": "Full-time"},
        "createdAt": 1710000000000,
    }
]

SMARTRECRUITERS = {
    "totalFound": 1,
    "content": [
        {
            "id": "744000152406179",
            "name": "ML Platform Engineer",
            "location": {"city": "Berlin", "region": "", "country": "de", "remote": False},
            "releasedDate": "2026-09-01T00:00:00.000Z",
        }
    ],
}

PERSONIO = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>1834171</id>
    <name>Platform Engineer</name>
    <office>Munich</office>
    <additionalOffices><office>Berlin</office></additionalOffices>
    <jobDescriptions>
      <jobDescription><name>Intro</name><value>&lt;p&gt;Wir suchen dich!&lt;/p&gt;</value></jobDescription>
    </jobDescriptions>
  </position>
</workzag-jobs>"""


WORKABLE = {
    "name": "Runware",
    "jobs": [
        {
            "title": "Senior Site Reliability Engineer",
            "shortcode": "7869EFBE16",
            "url": "https://apply.workable.com/j/7869EFBE16",
            "telecommuting": True,
            "published_on": "2026-07-20",
            "locations": [
                {"country": "United Kingdom", "countryCode": "GB", "city": ""},
                {"country": "France", "countryCode": "FR", "city": ""},
                {"country": "Germany", "countryCode": "DE", "city": ""},
            ],
            "description": "Kubernetes, observability",
        },
        {
            "title": "Cooling Systems Design Engineer",
            "shortcode": "FF7C5B881F",
            "telecommuting": False,
            "published_on": "2026-10-06",
            "country": "Romania",
            "city": "Bucharest",
            "description": "",
        },
    ],
}


def test_ashby_maps_fields_and_skips_unlisted() -> None:
    jobs = ats.fetch_ashby(StubFetcher(ASHBY), "vClusterLabs", "vCluster Labs")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.company == "vCluster Labs"
    assert job.location == "Remote"
    assert job.published == "2026-04-27"
    assert job.remote is True
    assert job.url.startswith("https://jobs.ashbyhq.com/vClusterLabs/")


def test_greenhouse_unwraps_location_and_first_published() -> None:
    job = ats.fetch_greenhouse(StubFetcher(GREENHOUSE), "gitlab", "GitLab")[0]
    assert job.location == "Remote, Bangalore"
    assert job.published == "2026-05-22"
    assert job.url == "https://job-boards.greenhouse.io/gitlab/jobs/8556658002"
    # Greenhouse escapes its `content` twice; the JD must reach the language gate as text.
    assert job.description == "About GitLab Kubernetes and Terraform at scale."


def test_lever_converts_epoch_millis_to_date() -> None:
    job = ats.fetch_lever(StubFetcher(LEVER), "acme", "Acme")[0]
    assert job.published == "2024-03-09"
    assert job.location == "Berlin"
    assert job.url == "https://jobs.lever.co/acme/123"


def test_lever_error_object_is_rejected() -> None:
    """Lever answers unknown boards with a 200 JSON object, not a list."""
    with pytest.raises(ValueError):
        ats.fetch_lever(StubFetcher({"ok": False, "error": "Document not found"}), "nope", "Nope")


def test_smartrecruiters_builds_public_url_and_joins_location() -> None:
    job = ats.fetch_smartrecruiters(StubFetcher(SMARTRECRUITERS), "BoschGroup", "Bosch")[0]
    assert job.url == "https://jobs.smartrecruiters.com/BoschGroup/744000152406179"
    assert job.location == "Berlin, de"
    assert job.published == "2026-09-01"


def test_smartrecruiters_empty_board_yields_no_jobs() -> None:
    assert ats.fetch_smartrecruiters(StubFetcher({"totalFound": 0, "content": []}), "empty", "Empty") == []


def test_personio_parses_offices() -> None:
    job = ats.fetch_personio(StubFetcher(text=PERSONIO), "personio", "Personio")[0]
    assert job.title == "Platform Engineer"
    assert job.location == "Munich, Berlin"
    assert job.url == "https://personio.jobs.personio.de/job/1834171"
    # JD text is needed for language detection
    assert job.description == "Wir suchen dich!"


def test_workable_joins_countries_from_locations() -> None:
    """The widget payload has no `location` key — geography is in `locations[]`, and a
    multi-country requisition carries one entry per country."""
    jobs = ats.fetch_workable(StubFetcher(WORKABLE), "runware", "runware")
    remote_role, onsite_role = jobs
    assert remote_role.location == "United Kingdom; France; Germany"
    assert remote_role.remote is True
    assert remote_role.published == "2026-07-20"
    assert remote_role.url == "https://apply.workable.com/j/7869EFBE16"
    # A payload with no locations[] falls back to the flat city/country pair.
    assert onsite_role.location == "Bucharest, Romania"
    assert onsite_role.remote is False
    assert onsite_role.url == "https://apply.workable.com/runware/j/FF7C5B881F/"
