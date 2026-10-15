"""Many-employer ATS adapters: field mapping, request bodies and pager stops."""

from __future__ import annotations

import pytest

from jobradar.sources import ats_extra


class Response:
    def __init__(self, payload=None, status: int = 200) -> None:
        self.payload = payload
        self.status_code = status

    def json(self):
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class StubFetcher:
    """Serves captured payloads. Route values may be callables, to vary by request."""

    def __init__(self, routes: dict | None = None, posts: dict | None = None, text: str = "") -> None:
        self.routes = routes or {}
        self.post_routes = posts or {}
        self.body = text
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []
        self.jsons: list[str] = []

    def _pick(self, routes: dict, url: str, payload=None):
        for fragment, value in routes.items():
            if fragment in url:
                if isinstance(value, Exception):
                    raise value
                return value(url, payload) if callable(value) else value
        return None

    def get(self, url: str, tries: int = 2, headers=None) -> Response:
        self.gets.append(url)
        return Response(self._pick(self.routes, url))

    def post(self, url: str, payload, tries: int = 2, headers=None) -> Response:
        self.posts.append((url, payload))
        return Response(self._pick(self.post_routes, url, payload))

    def json(self, url: str):
        self.jsons.append(url)
        return self._pick(self.routes, url)

    def text(self, url: str) -> str:
        return self.body


def _workday_list(url: str, payload=None) -> dict:
    base = (payload or {}).get("offset", 0)
    return {
        "total": 2000,
        "jobPostings": [
            {
                "title": f"STAGE 2027 - Manufacturing Engineering {base + n}",
                "externalPath": f"/job/Toulouse-Area/STAGE-{base + n}",
                "locationsText": "Toulouse Area",
                "postedOn": "Posted Today",
                "bulletFields": [f"JR{base + n}"],
            }
            for n in range(20)
        ],
    }


WORKDAY_DETAIL = {
    "jobPostingInfo": {
        "id": "023aba191f86104430a46abb39d10000",
        "title": "STAGE 2027 - Manufacturing Engineering Transformation (h/f)",
        "location": "Toulouse Area",
        "startDate": "2026-10-15",
        "timeType": "Full time",
        "jobReqId": "JR10441167",
        "externalUrl": "https://ag.wd3.myworkdayjobs.com/Airbus/job/Toulouse-Area/STAGE-2027_JR10441167",
        "jobDescription": "<p>Vous rejoindrez <strong>Airbus</strong> à Toulouse.</p>",
    },
    "hiringOrganization": {"name": "Airbus"},
}


SOFTGARDEN = {
    "meta": {"source": "https://areas.career.softgarden.de/"},
    "@type": "DataFeed",
    "numberOfItems": 2,
    "dataFeedElement": [
        {
            "@type": "DataFeedItem",
            "dateModified": "2026-10-15T04:53:57.000Z",
            "item": {
                "@type": "JobPosting",
                "title": "Kok på Center Parcs Nordborg Resort",
                "url": "https://areas.career.softgarden.de/jobs/64525803/Kok/",
                "datePosted": "2026-10-08T15:29:24.463+02:00",
                "identifier": {"@type": "PropertyValue", "name": "Areas Deutschland Holding GmbH", "value": 64525803},
                "employmentType": "OTHER",
                "hiringOrganization": {"@type": "Organization", "name": "Areas Deutschland Holding GmbH"},
                "jobLocation": {
                    "@type": "Place",
                    "address": {
                        "@type": "PostalAddress",
                        "postalCode": "6430",
                        "addressRegion": "-",
                        "addressCountry": "Dänemark",
                        "addressLocality": "Nordborg Sogn",
                    },
                },
                "description": "<p>Vi søger en <strong>kok</strong> til vores resort.</p>",
            },
        }
    ],
}

SOFTGARDEN_ERROR = {
    "success": False,
    "errorMessage": "No such client for domain dogado.career.softgarden.de",
}

JIBE = {
    "jobs": [
        {
            "data": {
                "slug": "36982",
                "req_id": "36982",
                "title": "Data Scientist F/H",
                "description": "<p>Rejoignez l'équipe <em>data</em> d'AXA.</p>",
                "full_location": "PUTEAUX, France",
                "location_name": None,
                "city": "PUTEAUX",
                "country": "France",
                "posted_date": "2026-10-14T10:00:00+0000",
                "employment_type": "FULL_TIME",
                "languages": ["fr-fr"],
                "apply_url": "https://candidature-recrutement.axa.fr/parcours/JobDetail?pipelineId=36982",
                "hiring_organization": "AXA",
            }
        },
        {
            "data": {
                "slug": "29143",
                "req_id": "29143",
                "title": "Beca de Desarrollo",
                "description": "<p>Beca de desarrollo y traducción de programas.</p>",
                "full_location": "MADRID, Spain",
                "city": "MADRID",
                "country": "Spain",
                "posted_date": "2026-10-14T11:02:00+0000",
                "employment_type": "INTERN",
                "languages": ["es-es"],
                "apply_url": "https://careers-es-axa.icims.com/jobs/29143/login",
                "hiring_organization": "AXA",
            }
        },
    ],
    "locations": [],
    "totalCount": 1596,
    "count": 953,
}

SF_RSS = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version='2.0' xmlns:atom='http://www.w3.org/2005/Atom'><channel>
  <atom:link href='https://careers.eon.com/feeds//?locale=en_US' rel='self' type='application/rss+xml' />
  <title>E.ON - Custom</title>
  <link>https://careers.eon.com/feeds//?locale=en_US</link>
  <item>
    <title><![CDATA[Senior Manager Corporate Development (Essen, DE)]]></title>
    <link>https://careers.eon.com/westconnect/job/Essen-Senior-Manager/1437528133/</link>
    <description><![CDATA[<p><strong>Westconnect GmbH</strong> sucht Verstärkung.</p>]]></description>
    <pubDate>Thu, 15 Oct 2026 2:00:00 GMT</pubDate>
    <guid isPermaLink="true">https://careers.eon.com/westconnect/job/Essen-Senior-Manager/1437528133/</guid>
  </item>
  <item>
    <title><![CDATA[Elektroniker (Bad Segeberg, DE)]]></title>
    <link>https://careers.eon.com/hansewerk/job/Bad-Segeberg-Elektroniker/1367382333/</link>
    <description><![CDATA[<p>Servicetechniker Umspannwerke.</p>]]></description>
    <pubDate>Wed, 14 Oct 2026 2:00:00 GMT</pubDate>
    <guid isPermaLink="true">https://careers.eon.com/hansewerk/job/Bad-Segeberg-Elektroniker/1367382333/</guid>
  </item>
</channel></rss>"""

JOIN = {
    "data": {
        "publicJobs": {
            "items": [
                {
                    "id": 16197447,
                    "title": "Intern (w/m/d) Learning & Development DaF",
                    "status": "ONLINE",
                    "createdAt": "2026-05-22T13:43:53.214Z",
                    "descriptionHtml": "<p>Unser Ziel ist eine <strong>Solaranlage</strong> auf jedem Dach.</p>",
                    "company": {"id": 12151, "name": "Enpal B.V."},
                    "city": {"cityName": "Blankenfelde-Mahlow"},
                    "country": {"name": "Germany"},
                    "employmentType": {"name": "Internship"},
                },
                {
                    "id": 16197448,
                    "title": "Archived role",
                    "status": "ARCHIVED",
                    "createdAt": "2026-04-01T10:00:00.000Z",
                    "descriptionHtml": "<p>Gone.</p>",
                    "company": {"id": 12151, "name": "Enpal B.V."},
                },
            ]
        }
    }
}


def test_workday_posts_the_documented_body() -> None:
    fetcher = StubFetcher(routes={"wday": WORKDAY_DETAIL}, posts={"jobs": _workday_list})
    ats_extra.fetch_workday(fetcher, "ag.wd3/Airbus", "Airbus")
    url, payload = fetcher.posts[0]
    assert url == "https://ag.wd3.myworkdayjobs.com/wday/cxs/ag/Airbus/jobs"
    assert payload == {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}


def test_workday_stops_at_offset_2000_and_carries_the_jd_text() -> None:
    fetcher = StubFetcher(routes={"wday": WORKDAY_DETAIL}, posts={"jobs": _workday_list})
    jobs = ats_extra.fetch_workday(fetcher, "ag.wd3/Airbus", "Airbus")
    offsets = [payload["offset"] for _, payload in fetcher.posts]
    assert offsets == list(range(0, 2000, 20))
    assert len(jobs) == 2000
    first = jobs[0]
    assert first.source == "workday:ag/Airbus"
    assert first.location == "Toulouse Area"
    assert first.published == "2026-10-15"
    assert first.extra["req_id"] == "JR0"
    assert first.extra["time_type"] == "Full time"
    # The list row has no JD: the description and the canonical URL come from the detail call.
    assert first.description == "Vous rejoindrez Airbus à Toulouse."
    assert first.url == "https://ag.wd3.myworkdayjobs.com/Airbus/job/Toulouse-Area/STAGE-2027_JR10441167"


def test_workday_keeps_rows_when_a_detail_call_fails() -> None:
    fetcher = StubFetcher(routes={"wday": RuntimeError("connection reset")}, posts={"jobs": _workday_list})
    jobs = ats_extra.fetch_workday(fetcher, "ag.wd3/Airbus", "Airbus")
    assert len(jobs) == 2000
    assert jobs[0].description == ""
    assert jobs[0].url == "https://ag.wd3.myworkdayjobs.com/Airbus/job/Toulouse-Area/STAGE-0"


def test_workday_token_rejects_a_bare_tenant() -> None:
    with pytest.raises(ValueError):
        ats_extra.fetch_workday(StubFetcher(), "airbus", "Airbus")


def test_softgarden_maps_the_data_feed() -> None:
    jobs = ats_extra.fetch_softgarden(StubFetcher({"softgarden": SOFTGARDEN}), "areas", "Areas")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.company == "Areas Deutschland Holding GmbH"
    assert job.location == "Nordborg Sogn, Dänemark"  # softgarden writes "-" for absent fields
    assert job.published == "2026-10-08"
    assert job.description == "Vi søger en kok til vores resort."
    assert job.source == "softgarden:areas"


def test_softgarden_unknown_client_is_an_error_not_an_empty_board() -> None:
    with pytest.raises(ValueError, match="No such client"):
        ats_extra.fetch_softgarden(StubFetcher({"softgarden": SOFTGARDEN_ERROR}), "dogado", "Dogado")


def test_softgarden_accepts_a_full_host_token() -> None:
    fetcher = StubFetcher({"softgarden": SOFTGARDEN})
    ats_extra.fetch_softgarden(fetcher, "areas.career.softgarden.de", "Areas")
    assert fetcher.jsons == ["https://areas.career.softgarden.de/jobs.feed.json"]


def test_icims_jibe_maps_the_search_payload() -> None:
    fetcher = StubFetcher({"api/jobs": JIBE})
    jobs = ats_extra.fetch_icims_jibe(fetcher, "careers.axa.com", "AXA")
    assert fetcher.routes and len(jobs) == 2
    job = jobs[0]
    assert job.company == "AXA"
    assert job.url == "https://candidature-recrutement.axa.fr/parcours/JobDetail?pipelineId=36982"
    assert job.location == "PUTEAUX, France"
    assert job.published == "2026-10-14"
    assert job.description == "Rejoignez l'équipe data d'AXA."
    assert job.extra["languages"] == ["fr-fr"]
    assert job.source == "icims:careers.axa.com"


def test_icims_jibe_stops_on_a_short_page() -> None:
    fetcher = StubFetcher({"api/jobs": JIBE})
    ats_extra.fetch_icims_jibe(fetcher, "careers.axa.com", "AXA")
    assert fetcher.jsons == ["https://careers.axa.com/api/jobs?limit=100&page=1"]


def test_icims_jibe_count_bounds_the_pager() -> None:
    full = {"jobs": JIBE["jobs"] * 50, "count": 100, "totalCount": 1596}
    fetcher = StubFetcher({"page=1": full, "page=2": {"jobs": [], "count": 100}})
    jobs = ats_extra.fetch_icims_jibe(fetcher, "careers.axa.com", "AXA")
    assert len(jobs) == 100  # `count` is reached on page 1, so page 2 is never requested


def test_successfactors_reads_the_twenty_item_window() -> None:
    jobs = ats_extra.fetch_successfactors(StubFetcher(text=SF_RSS), "careers.eon.com", "E.ON")
    assert len(jobs) == 2
    job = jobs[0]
    assert job.title == "Senior Manager Corporate Development (Essen, DE)"
    assert job.url == "https://careers.eon.com/westconnect/job/Essen-Senior-Manager/1437528133/"
    assert job.published == "2026-10-15"
    assert job.description == "Westconnect GmbH sucht Verstärkung."
    assert job.source == "successfactors:careers.eon.com"


def test_join_com_posts_the_public_jobs_query() -> None:
    fetcher = StubFetcher(posts={"graphql": JOIN})
    jobs = ats_extra.fetch_join_com(fetcher, "12151", "Enpal")
    url, payload = fetcher.posts[0]
    assert url == "https://join.com/candidate-api/graphql"
    assert "publicJobs" in payload["query"]
    assert payload["variables"]["input"] == {"companyId": 12151, "paginationPage": {"page": 1, "pageSize": 50}}
    assert len(jobs) == 1  # the ARCHIVED row is dropped
    job = jobs[0]
    assert job.company == "Enpal B.V."
    assert job.url == "https://join.com/jobs/16197447"
    assert job.location == "Blankenfelde-Mahlow, Germany"
    assert job.published == "2026-05-22"
    assert job.description == "Unser Ziel ist eine Solaranlage auf jedem Dach."
    assert job.source == "join_com:12151"


def test_join_com_resolves_a_slug_token_from_the_board_page() -> None:
    page = '<script>window.__INITIAL_STATE__ = {"jobAd":{"company":{"id":12151,"slug":"enpal"}}}</script>'
    fetcher = StubFetcher(posts={"graphql": JOIN}, text=page)
    jobs = ats_extra.fetch_join_com(fetcher, "enpal", "Enpal")
    assert fetcher.posts[0][1]["variables"]["input"]["companyId"] == 12151
    assert len(jobs) == 1


def test_join_com_slug_without_a_company_id_raises() -> None:
    with pytest.raises(ValueError, match="no companyId"):
        ats_extra.fetch_join_com(StubFetcher(text="<html></html>"), "nope", "Nope")
