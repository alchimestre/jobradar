"""Many-employer ATS platform adapters. Every endpoint here is public and unauthenticated.

Each one unlocks every employer on its platform, so the token is a tenant, not a board.
Workday alone carries the deep French and German employer base (Airbus, Sanofi, Eiffage,
Evonik, Ipsen, Covestro, Trumpf, Zalando, Stellantis).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..models import Job
from ..net import Fetcher
from .ats import _date
from .feeds import _rss_date
from .helpers import gather, strip_html

# Workday caps `limit` at 20 (21 is HTTP 400) and silently wraps `offset` back to the
# first page at 2000, so `0 <= offset < 2000` is the whole enumerable range.
WORKDAY_PAGE = 20
WORKDAY_MAX_OFFSET = 2000
# The list payload has no JD text and the JD-language gate needs it, so each description
# costs one detail call. Bounded per company per scan.
WORKDAY_DETAIL_LIMIT = 200
# Serial, that limit is 200 round trips and the slowest thing in a scan.
WORKDAY_DETAIL_WORKERS = 6

JIBE_PAGE = 100
JIBE_MAX_PAGES = 20

JOIN_ENDPOINT = "https://join.com/candidate-api/graphql"
JOIN_PAGE = 50
JOIN_MAX_PAGES = 10

_SCHEME = re.compile(r"^https?://", re.I)
_LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$", re.I)


def _host(token: str) -> str:
    return _SCHEME.sub("", token.strip().rstrip("/")).partition("/")[0]


def _softgarden_host(token: str) -> str:
    host = _host(token)
    return host if "." in host else f"{host}.career.softgarden.de"


def _workday_parts(token: str) -> tuple[str, str, str]:
    """`ag.wd3/Airbus` -> ("ag.wd3.myworkdayjobs.com", "ag", "Airbus").

    Also accepts the browser form with the locale (`ag.wd3.myworkdayjobs.com/en-US/Airbus`)
    and a token that already carries the full host.
    """
    host, _, rest = _SCHEME.sub("", token.strip().rstrip("/")).partition("/")
    if "." not in host:
        raise ValueError(f"workday token '{token}' needs the tenant host, e.g. ag.wd3/Airbus")
    if not host.lower().endswith(".myworkdayjobs.com"):
        host = f"{host}.myworkdayjobs.com"
    segments = [s for s in rest.split("/") if s]
    if segments and _LOCALE.match(segments[0]):
        segments = segments[1:]
    if not segments:
        raise ValueError(f"workday token '{token}' is missing the career-site slug, e.g. ag.wd3/Airbus")
    return host, host.split(".")[0].lower(), segments[0]


def _workday_detail(fetcher: Fetcher, url: str) -> dict:
    try:
        response = fetcher.get(url)
        response.raise_for_status()
        return (response.json() or {}).get("jobPostingInfo") or {}
    except Exception:  # one unreachable posting must not cost the whole board
        return {}


def _workday_getter(fetcher: Fetcher, url: str):
    return lambda: _workday_detail(fetcher, url)


def fetch_workday(fetcher: Fetcher, token: str, company: str) -> list[Job]:
    host, tenant, site = _workday_parts(token)
    board = f"https://{host}/wday/cxs/{tenant}/{site}"
    rows: list[dict] = []
    offset = 0
    while offset < WORKDAY_MAX_OFFSET:
        response = fetcher.post(
            f"{board}/jobs",
            {"appliedFacets": {}, "limit": WORKDAY_PAGE, "offset": offset, "searchText": ""},
        )
        response.raise_for_status()
        page = (response.json() or {}).get("jobPostings") or []
        rows.extend(page)
        if len(page) < WORKDAY_PAGE:
            break
        offset += WORKDAY_PAGE

    detail_rows = [
        index
        for index, row in enumerate(rows)
        if (row.get("externalPath") or "") and index < WORKDAY_DETAIL_LIMIT
    ]
    infos = gather(
        [_workday_getter(fetcher, f"{board}{rows[index].get('externalPath')}") for index in detail_rows],
        workers=WORKDAY_DETAIL_WORKERS,
    )
    details = dict(zip(detail_rows, infos))

    out = []
    for index, row in enumerate(rows):
        external = row.get("externalPath") or ""
        url = f"https://{host}/{site}{external}"
        info = details.get(index) or {}
        description = strip_html(info.get("jobDescription"))
        published = _date(info.get("startDate"))
        if info:
            url = info.get("externalUrl") or url
        time_type = info.get("timeType")
        out.append(
            Job(
                company=company,
                title=row.get("title", ""),
                url=url,
                source=f"workday:{tenant}/{site}",
                location=row.get("locationsText") or "",
                published=published,
                remote=None,
                description=description,
                extra={"req_id": (row.get("bulletFields") or [""])[0], "time_type": time_type},
            )
        )
    return out


def fetch_softgarden(fetcher: Fetcher, token: str, company: str) -> list[Job]:
    """softgarden career sites. The whole board arrives in one schema.org `DataFeed`."""
    payload = fetcher.json(f"https://{_softgarden_host(token)}/jobs.feed.json")
    rows = payload.get("dataFeedElement")
    if rows is None:
        raise ValueError(f"softgarden board '{token}': {payload.get('errorMessage') or 'no dataFeedElement'}")
    out = []
    for row in rows:
        item = row.get("item") or {}
        address = (item.get("jobLocation") or {}).get("address") or {}
        # softgarden writes "-" for an address field that has no value.
        parts = [str(address.get(key)) for key in ("addressLocality", "addressRegion", "addressCountry")]
        out.append(
            Job(
                company=(item.get("hiringOrganization") or {}).get("name") or company,
                title=item.get("title", ""),
                url=item.get("url", ""),
                source=f"softgarden:{token}",
                location=", ".join(p for p in parts if p and p != "-"),
                published=_date(item.get("datePosted")),
                remote=None,
                description=strip_html(item.get("description")),
                extra={
                    "id": (item.get("identifier") or {}).get("value"),
                    "employment_type": item.get("employmentType"),
                },
            )
        )
    return out


def fetch_icims_jibe(fetcher: Fetcher, token: str, company: str) -> list[Job]:
    """iCIMS sites fronted by the Jibe search app. `/api/jobs` is capped at 100 per page."""
    host = _host(token)
    out = []
    for page in range(1, JIBE_MAX_PAGES + 1):
        payload = fetcher.json(f"https://{host}/api/jobs?limit={JIBE_PAGE}&page={page}")
        rows = payload.get("jobs") or []
        for row in rows:
            data = row.get("data") or {}
            slug = data.get("slug") or data.get("req_id") or ""
            location = data.get("full_location") or data.get("short_location")
            if not location:
                location = ", ".join(str(data.get(key)) for key in ("city", "country") if data.get(key))
            out.append(
                Job(
                    company=data.get("hiring_organization") or company,
                    title=data.get("title", ""),
                    url=data.get("apply_url") or f"https://{host}/jobs/{slug}",
                    source=f"icims:{host}",
                    location=location,
                    published=_date(data.get("posted_date")),
                    remote=None,
                    description=strip_html(data.get("description")),
                    extra={
                        "req_id": data.get("req_id"),
                        "employment_type": data.get("employment_type"),
                        "languages": data.get("languages"),
                    },
                )
            )
        # `count` is what this site's default filter returns; `totalCount` counts the
        # whole tenant board, which is more than these pages can enumerate.
        count = payload.get("count")
        if len(rows) < JIBE_PAGE or (isinstance(count, int) and page * JIBE_PAGE >= count):
            break
    return out


def fetch_successfactors(fetcher: Fetcher, token: str, company: str) -> list[Job]:
    """SAP SuccessFactors Recruiting Marketing career sites.

    `services/rss/job` is a fixed window of the 20 newest postings: `?startrow=`,
    `?numberOfItems=`, `?feedId=` and `?q=` all return the same 20. It is a refresh
    signal, not the board — the full list is server-rendered HTML under
    `/search/?q=&startrow=N`, 25 jobs to a page.
    """
    host = _host(token)
    root = ET.fromstring(fetcher.text(f"https://{host}/services/rss/job"))
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or item.findtext("guid") or "").strip()
        if not title or not url:
            continue
        out.append(
            Job(
                company=company,
                title=title,
                url=url,
                source=f"successfactors:{host}",
                published=_rss_date(item.findtext("pubDate") or ""),
                description=strip_html(item.findtext("description")),
            )
        )
    return out


JOIN_QUERY = """query PublicJobs($input: PublicJobsQueryInput!) {
  publicJobs(input: $input) {
    items {
      id
      title
      status
      createdAt
      descriptionHtml
      company { id name }
      city { cityName }
      country { name }
      employmentType { name }
    }
  }
}"""


def _join_company_id(fetcher: Fetcher, token: str) -> int:
    """A join.com board is keyed by a numeric companyId, not by the public slug."""
    if token.strip().isdigit():
        return int(token.strip())
    slug = re.sub(r"^join\.com/companies/", "", _SCHEME.sub("", token.strip().rstrip("/")))
    slug = slug.partition("/")[0]
    page = fetcher.text(f"https://join.com/companies/{slug}")
    found = re.search(r'"company":\{"id":(\d+)', page)
    if not found:
        raise ValueError(f"join.com company '{token}': no companyId on the board page")
    return int(found.group(1))


def fetch_join_com(fetcher: Fetcher, token: str, company: str) -> list[Job]:
    company_id = _join_company_id(fetcher, token)
    out = []
    for page in range(1, JOIN_MAX_PAGES + 1):
        response = fetcher.post(
            JOIN_ENDPOINT,
            {
                "query": JOIN_QUERY,
                "variables": {
                    "input": {
                        "companyId": company_id,
                        "paginationPage": {"page": page, "pageSize": JOIN_PAGE},
                    }
                },
            },
        )
        response.raise_for_status()
        items = ((response.json() or {}).get("data") or {}).get("publicJobs", {}).get("items") or []
        for item in items:
            if (item.get("status") or "ONLINE") != "ONLINE":
                continue
            city = (item.get("city") or {}).get("cityName")
            country = (item.get("country") or {}).get("name")
            out.append(
                Job(
                    company=(item.get("company") or {}).get("name") or company,
                    title=item.get("title", ""),
                    # The API returns no job URL; the id form redirects to the posting page.
                    url=f"https://join.com/jobs/{item.get('id')}",
                    source=f"join_com:{company_id}",
                    location=", ".join(str(p) for p in (city, country) if p),
                    published=_date(item.get("createdAt")),
                    remote=None,
                    description=strip_html(item.get("descriptionHtml")),
                    extra={
                        "company_id": company_id,
                        "employment_type": (item.get("employmentType") or {}).get("name"),
                    },
                )
            )
        if len(items) < JOIN_PAGE:
            break
    return out


FETCHERS = {
    "workday": fetch_workday,
    "successfactors": fetch_successfactors,
    "icims_jibe": fetch_icims_jibe,
    "softgarden": fetch_softgarden,
    "join_com": fetch_join_com,
}
