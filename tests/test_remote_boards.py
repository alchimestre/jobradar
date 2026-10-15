"""Remote board adapters: Jobicy, Himalayas, Working Nomads, Landing.jobs, jobdataapi,
DevITjobs, The Muse."""

from __future__ import annotations

import pytest

from jobradar.sources import remote_boards
from jobradar.sources.helpers import BROWSER_UA


class Stub:
    """Route stub: `.json(url, headers=)` / `.text(url, headers=)`, recording every call."""

    def __init__(self, routes: dict[str, object] | None = None, text: str = "") -> None:
        self.routes = routes or {}
        self.body = text
        self.calls: list[tuple[str, dict | None]] = []

    def json(self, url: str, headers: dict | None = None):
        self.calls.append((url, headers))
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        return None

    def text(self, url: str, headers: dict | None = None) -> str:
        self.calls.append((url, headers))
        return self.body


def jobicy_row(ident: int, title: str = "Platform Engineer") -> dict:
    return {
        "id": ident,
        "url": f"https://jobicy.com/jobs/{ident}-platform-engineer",
        "jobTitle": title,
        "companyName": "Thumbtack",
        "jobGeo": "Canada",
        "jobLevel": "Senior",
        "jobType": ["Full-Time"],
        "jobIndustry": ["DevOps & Infrastructure"],
        "jobDescription": "<h3><strong>Thumbtack</strong></h3><p>Own our kubernetes platform.</p>",
        "pubDate": "2026-10-15T04:30:05+00:00",
        "salaryMin": 123300,
        "salaryMax": 159500,
        "salaryCurrency": "CAD",
    }


JOBICY_PAGE_1 = {"jobCount": 2, "hasMore": True, "nextCursor": "c2", "jobs": [jobicy_row(1), jobicy_row(2)]}
JOBICY_PAGE_2 = {"jobCount": 2, "hasMore": False, "nextCursor": "", "jobs": [jobicy_row(3), jobicy_row(4)]}

HIMALAYAS_ROW = {
    "title": "Associate Director, Development Outsourcing",
    "companyName": "Jade Biosciences",
    "applicationLink": "https://himalayas.app/companies/jade-biosciences/jobs/associate-director",
    "guid": "https://himalayas.app/companies/jade-biosciences/jobs/associate-director",
    "locationRestrictions": ["United States", "Canada"],
    "employmentType": "Full Time",
    "seniority": ["Director"],
    "categories": ["Operations"],
    "description": "<div><h3>About Jade</h3><p>Clinical-stage biotech.</p></div>",
    "excerpt": "About Jade Biosciences",
    "pubDate": 1792063003,
}
HIMALAYAS_PAGE_1 = {"totalCount": 123330, "limit": 2, "offset": 0, "jobs": [HIMALAYAS_ROW]}
HIMALAYAS_PAGE_2 = {"totalCount": 123330, "limit": 2, "offset": 1, "jobs": [HIMALAYAS_ROW]}

WORKING_NOMADS = [
    {
        "url": "https://www.workingnomads.com/job/go/1939844/",
        "title": "Language specialist",
        "description": "<h1>Remote Language Projects</h1><p>Native &amp; fluent speakers.</p>",
        "company_name": "iMerit Technology",
        "category_name": "Administration",
        "tags": "",
        "location": "Global",
        "pub_date": "2026-10-14T00:32:03-04:00",
    },
    {
        "url": "https://www.workingnomads.com/job/go/2/",
        "title": "SRE",
        "description": "<p>Run the clusters.</p>",
        "company_name": "Acme",
        "category_name": "DevOps",
        "tags": "kubernetes",
        "location": "Europe",
        "pub_date": "2026-10-13T10:00:00+02:00",
    },
]

LANDING_JOBS = [
    {
        "id": 19066,
        "title": "Senior Java Software Developer",
        "url": "https://landing.jobs/at/critical-software/senior-java-software-developer-in-lisbon-2025",
        "role_description": "<div><p>Our client is a global retail chain.</p></div>",
        "main_requirements": "<ul><li>6-8 years of experience with Java.</li></ul>",
        "relocation_paid": True,
        "remote": False,
        "locations": [{"city": "Lisbon", "country_code": "PT"}],
        "published_at": "2025-02-26T09:38:38.127Z",
        "tags": ["Java", "Kubernetes"],
        "gross_salary_low": 50000,
        "gross_salary_high": 67000,
        "currency_code": "EUR",
    }
]

JOBDATAAPI = {
    "count": 178962,
    "next": "https://jobdataapi.com/api/jobs/?country_code=FR&page=2",
    "previous": None,
    "results": [
        {
            "id": 59513317,
            "title": "AI Outcome Customer Engineer, Google Cloud",
            "company": {"id": 25600, "name": "Google"},
            "location": "Paris, France",
            "application_url": "https://www.google.com/about/careers/applications/jobs/results/120689860435419846",
            "has_remote": True,
            "published": "2026-10-15T11:12:34Z",
            "description": "<div><h3>Minimum qualifications:</h3><ul><li>Bachelor's degree.</li></ul></div>",
            "language": "en",
            "experience_level": "SE",
            "types": [{"id": 1, "name": "Full Time"}],
            "countries": [{"id": 78, "code": "FR", "name": "France"}],
        }
    ],
}

DEVIT_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>DevITJobs.uk - tech &amp; IT jobs</title>
    <item>
      <title><![CDATA[Platform Engineer @ Blue Box IT [£70,000 - 90,000]]]></title>
      <link>https://devitjobs.uk/jobs/Blue-Box-IT-Platform-Engineer?utm_source=our_rss_feed&amp;utm_medium=our_rss_feed</link>
      <guid isPermaLink="false">https://devitjobs.uk/jobs/Blue-Box-IT-Platform-Engineer?utm_source=our_rss_feed</guid>
      <pubDate>Thu, 15 Oct 2026 08:24:52 GMT</pubDate>
      <description><![CDATA[<p>teaser only</p>]]></description>
      <content:encoded><![CDATA[<p><b>Salary: £70,000 - 90,000 per year</b></p><b>Requirements:</b>
      <ul><li>Kubernetes and Terraform</li></ul>]]></content:encoded>
    </item>
  </channel>
</rss>"""

MUSE = {
    "page": 1,
    "page_count": 3,
    "total": 414096,
    "results": [
        {
            "id": 19225596,
            "name": "Site Reliability Engineer",
            "contents": "<p><b>Own the platform.</b></p><p>Kubernetes everywhere.</p>",
            "publication_date": "2026-10-01T09:00:00Z",
            "locations": [{"name": "Remote"}, {"name": "Berlin, Germany"}],
            "refs": {"landing_page": "https://www.themuse.com/jobs/acme/site-reliability-engineer"},
            "company": {"id": 15000905, "name": "Acme"},
            "levels": [{"name": "Mid Level"}],
            "categories": [{"name": "Engineering"}],
        }
    ],
}


# ---------------------------------------------------------------- jobicy


def test_jobicy_maps_fields_and_follows_the_cursor() -> None:
    stub = Stub({"cursor=c2": JOBICY_PAGE_2, "count=2": JOBICY_PAGE_1})
    jobs = remote_boards.fetch_jobicy(stub, {"name": "jobicy-remote", "count": 2, "max_pages": 2})
    assert len(jobs) == 4
    assert len(stub.calls) == 2
    first = jobs[0]
    assert first.company == "Thumbtack"
    assert first.title == "Platform Engineer"
    assert first.source == "jobicy:jobicy-remote"
    assert first.location == "Canada"
    assert first.published == "2026-10-15"
    assert first.remote is True
    assert first.description == "Thumbtack Own our kubernetes platform."
    assert first.extra["level"] == "Senior"
    assert first.extra["salary_max"] == 159500


def test_jobicy_stops_at_max_pages() -> None:
    stub = Stub({"cursor=c2": JOBICY_PAGE_2, "count=2": JOBICY_PAGE_1})
    jobs = remote_boards.fetch_jobicy(stub, {"name": "j", "count": 2})
    assert len(jobs) == 2
    assert len(stub.calls) == 1


def test_jobicy_max_items_stops_an_endless_pager() -> None:
    endless = {"jobCount": 5, "hasMore": True, "nextCursor": "more", "jobs": [jobicy_row(i) for i in range(5)]}
    stub = Stub({"remote-jobs": endless})
    jobs = remote_boards.fetch_jobicy(stub, {"name": "j", "count": 5, "max_pages": 9, "max_items": 7})
    assert len(jobs) == 7
    assert len(stub.calls) == 2


def test_jobicy_page_size_is_capped_at_the_observed_limit() -> None:
    stub = Stub({"remote-jobs": {"jobs": []}})
    remote_boards.fetch_jobicy(stub, {"name": "j", "count": 9999})
    assert "count=200" in stub.calls[0][0]


# ---------------------------------------------------------------- himalayas


def test_himalayas_maps_fields_and_advances_the_offset() -> None:
    stub = Stub({"offset=1": HIMALAYAS_PAGE_2, "offset=0": HIMALAYAS_PAGE_1})
    jobs = remote_boards.fetch_himalayas(stub, {"name": "himalayas", "limit": 2, "max_pages": 2})
    assert len(jobs) == 2
    assert len(stub.calls) == 2
    assert "offset=1" in stub.calls[1][0]
    first = jobs[0]
    assert first.company == "Jade Biosciences"
    assert first.source == "himalayas:himalayas"
    assert first.location == "United States, Canada"
    assert first.published == "2026-10-15"
    assert first.remote is True
    assert first.description == "About Jade Clinical-stage biotech."
    assert first.extra["seniority"] == ["Director"]


def test_himalayas_max_items_stops_an_endless_pager() -> None:
    endless = {"totalCount": 123330, "jobs": [HIMALAYAS_ROW] * 20}
    stub = Stub({"jobs/api": endless})
    jobs = remote_boards.fetch_himalayas(stub, {"name": "h", "limit": 20, "max_pages": 50, "max_items": 25})
    assert len(jobs) == 25
    assert len(stub.calls) == 2


def test_himalayas_page_size_is_capped_at_the_observed_limit() -> None:
    stub = Stub({"jobs/api": {"jobs": []}})
    remote_boards.fetch_himalayas(stub, {"name": "h", "limit": 500})
    assert "limit=20" in stub.calls[0][0]


# ---------------------------------------------------------------- working nomads


def test_workingnomads_maps_the_array() -> None:
    stub = Stub({"exposed_jobs": WORKING_NOMADS})
    jobs = remote_boards.fetch_workingnomads(stub, {"name": "workingnomads"})
    assert [job.title for job in jobs] == ["Language specialist", "SRE"]
    assert jobs[0].company == "iMerit Technology"
    assert jobs[0].source == "workingnomads:workingnomads"
    assert jobs[0].location == "Global"
    assert jobs[0].published == "2026-10-14"
    assert jobs[0].remote is True
    assert jobs[0].description == "Remote Language Projects Native & fluent speakers."
    assert jobs[0].extra["category"] == "Administration"


def test_workingnomads_honours_max_items() -> None:
    stub = Stub({"exposed_jobs": WORKING_NOMADS})
    jobs = remote_boards.fetch_workingnomads(stub, {"name": "workingnomads", "max_items": 1})
    assert len(jobs) == 1
    assert len(stub.calls) == 1


# ---------------------------------------------------------------- landing.jobs


def test_landingjobs_maps_description_company_and_flags() -> None:
    stub = Stub({"v1/jobs": LANDING_JOBS})
    job = remote_boards.fetch_landingjobs(stub, {"name": "landingjobs", "limit": 20})[0]
    assert job.company == "Critical Software"
    assert job.title == "Senior Java Software Developer"
    assert job.source == "landingjobs:landingjobs"
    assert job.location == "Lisbon, PT"
    assert job.published == "2025-02-26"
    assert job.remote is False
    assert "global retail chain" in job.description
    assert "6-8 years of experience with Java" in job.description
    assert job.extra["relocation_paid"] is True
    assert job.extra["salary_max"] == 67000


def test_landingjobs_stops_when_a_short_page_arrives() -> None:
    """A page smaller than `limit` is the last one, so no extra request is made."""
    stub = Stub({"v1/jobs": LANDING_JOBS})
    jobs = remote_boards.fetch_landingjobs(stub, {"name": "l", "limit": 20, "max_pages": 5})
    assert len(jobs) == 1
    assert len(stub.calls) == 1


def test_landingjobs_stops_at_max_pages() -> None:
    stub = Stub({"v1/jobs": LANDING_JOBS * 20})
    jobs = remote_boards.fetch_landingjobs(stub, {"name": "l", "limit": 20, "max_pages": 2, "max_items": 25})
    assert len(jobs) == 25
    assert len(stub.calls) == 2
    assert "offset=20" in stub.calls[1][0]


# ---------------------------------------------------------------- jobdataapi


def test_jobdataapi_sends_the_country_and_keeps_the_language() -> None:
    stub = Stub({"api/jobs/": JOBDATAAPI})
    job = remote_boards.fetch_jobdataapi(stub, {"name": "jobdataapi-fr", "country_code": "FR"})[0]
    assert "country_code=FR" in stub.calls[0][0]
    assert stub.calls[0][0].endswith("country_code=FR")  # page 1 omits `page`: 403 for anonymous
    assert job.company == "Google"
    assert job.title == "AI Outcome Customer Engineer, Google Cloud"
    assert job.source == "jobdataapi:jobdataapi-fr"
    assert job.location == "Paris, France"
    assert job.published == "2026-10-15"
    assert job.remote is True
    assert job.extra["language"] == "en"
    assert job.extra["countries"] == ["FR"]


def test_jobdataapi_pages_on_request() -> None:
    stub = Stub({"api/jobs/": JOBDATAAPI})
    jobs = remote_boards.fetch_jobdataapi(stub, {"name": "jda", "country_code": "FR", "max_pages": 2})
    assert len(jobs) == 2
    assert "page=2" in stub.calls[1][0]


def test_jobdataapi_honours_the_start_page() -> None:
    stub = Stub({"api/jobs/": JOBDATAAPI})
    remote_boards.fetch_jobdataapi(stub, {"name": "jda", "country_code": "FR", "page": 3})
    assert "page=3" in stub.calls[0][0]


def test_jobdataapi_requires_a_country() -> None:
    with pytest.raises(ValueError):
        remote_boards.fetch_jobdataapi(Stub({}), {"name": "jda"})


# ---------------------------------------------------------------- devitjobs


def test_devitjobs_parses_content_encoded_and_strips_tracking() -> None:
    stub = Stub(text=DEVIT_RSS)
    job = remote_boards.fetch_devitjobs(
        stub, {"name": "devitjobs-uk", "user_agent": "browser"}
    )[0]
    assert job.url == "https://devitjobs.uk/jobs/Blue-Box-IT-Platform-Engineer"
    assert job.company == "Blue Box IT"
    assert job.title == "Platform Engineer @ Blue Box IT [£70,000 - 90,000]"
    assert job.source == "devitjobs:devitjobs-uk"
    assert job.published == "2026-10-15"
    assert job.description == "Salary: £70,000 - 90,000 per year Requirements: Kubernetes and Terraform"
    assert stub.calls[0][1]["User-Agent"] == BROWSER_UA


def test_devitjobs_keeps_a_query_that_is_not_tracking() -> None:
    assert (
        remote_boards._without_tracking("https://devitjobs.uk/jobs/x?utm_source=our_rss_feed&ref=1")
        == "https://devitjobs.uk/jobs/x?ref=1"
    )
    assert remote_boards._without_tracking("https://devitjobs.uk/jobs/x") == "https://devitjobs.uk/jobs/x"


# ---------------------------------------------------------------- the muse


def test_themuse_maps_rows_and_sends_a_browser_agent() -> None:
    stub = Stub({"public/jobs": MUSE})
    job = remote_boards.fetch_themuse(stub, {"name": "themuse", "user_agent": "browser"})[0]
    assert job.company == "Acme"
    assert job.title == "Site Reliability Engineer"
    assert job.url == "https://www.themuse.com/jobs/acme/site-reliability-engineer"
    assert job.source == "themuse:themuse"
    assert job.location == "Remote, Berlin, Germany"
    assert job.published == "2026-10-01"
    assert job.remote is True
    assert job.description == "Own the platform. Kubernetes everywhere."
    assert job.extra["levels"] == ["Mid Level"]
    assert stub.calls[0][1]["User-Agent"] == BROWSER_UA


def test_themuse_pages_and_stops_at_max_pages() -> None:
    stub = Stub({"public/jobs": MUSE})
    jobs = remote_boards.fetch_themuse(stub, {"name": "m", "category": "Data and Analytics", "max_pages": 2})
    assert len(jobs) == 2
    assert len(stub.calls) == 2
    assert "page=2" in stub.calls[1][0]
    assert "category=Data+and+Analytics" in stub.calls[0][0]


def test_themuse_stops_on_the_last_page() -> None:
    last = dict(MUSE, page=3)
    stub = Stub({"public/jobs": last})
    jobs = remote_boards.fetch_themuse(stub, {"name": "m", "page": 3, "max_pages": 4})
    assert len(jobs) == 1
    assert len(stub.calls) == 1


# ---------------------------------------------------------------- registry


def test_fetchers_registry_covers_every_type() -> None:
    assert set(remote_boards.FETCHERS) == {
        "jobicy",
        "himalayas",
        "workingnomads",
        "landingjobs",
        "jobdataapi",
        "devitjobs",
        "themuse",
    }
