"""Many-employer remote boards: Jobicy, Himalayas, Working Nomads, Landing.jobs,
jobdataapi, DevITjobs RSS and The Muse.

DevITjobs answers 499 and The Muse 403 unless the request carries a browser UA, so
their config entries set `user_agent: browser`; `helpers.headers` turns that into one.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..models import Job
from ..net import Fetcher
from .helpers import bound, company_from_url, guess_company, headers, strip_html

JOBICY_API = "https://jobicy.com/api/v2/remote-jobs"
JOBICY_PAGE_MAX = 200  # observed: `count` above 200 still returns 200 rows
HIMALAYAS_API = "https://himalayas.app/jobs/api"
HIMALAYAS_PAGE_MAX = 20  # observed: `limit` is clamped to 20 server-side
WORKINGNOMADS_API = "https://www.workingnomads.com/api/exposed_jobs/"
LANDINGJOBS_API = "https://landing.jobs/api/v1/jobs"
JOBDATAAPI_API = "https://jobdataapi.com/api/jobs/"
DEVITJOBS_RSS = "https://devitjobs.uk/rss"
THEMUSE_API = "https://www.themuse.com/api/public/jobs"

_CONTENT_ENCODED = "{http://purl.org/rss/1.0/modules/content/}encoded"
_TRACKING_VALUE = "our_rss_feed"
_LANDING_COMPANY = re.compile(r"/at/([^/?#]+)")


def _json(fetcher: Fetcher, spec: dict, url: str):
    return fetcher.json(url, headers=headers(spec))


def _date(value) -> str:
    """`YYYY-MM-DD` from an epoch int, an ISO timestamp or an RFC-822 feed date."""
    if isinstance(value, (int, float)) and value:
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")
    text = str(value or "").strip()
    found = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if found:
        return found.group(1)
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _capped(rows: list[Job], max_items: int) -> list[Job]:
    return rows[:max_items] if max_items > 0 else rows


def _query(params: dict) -> str:
    return "?" + urlencode({key: value for key, value in params.items() if value not in (None, "")})


def _without_tracking(url: str) -> str:
    """DevITjobs appends `?utm_source=our_rss_feed&utm_medium=our_rss_feed` to every link."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [(key, value) for key, value in parse_qsl(parts.query) if value != _TRACKING_VALUE]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def _landing_company(url: str, title: str, fallback: str) -> str:
    """Landing.jobs puts the employer in the path: `landing.jobs/at/inscale/<role>`."""
    found = _LANDING_COMPANY.search(url or "")
    if found:
        return found.group(1).replace("-", " ").replace("_", " ").title()
    return company_from_url(url) or guess_company(title, fallback)


def fetch_jobicy(fetcher: Fetcher, spec: dict) -> list[Job]:
    """Cursor-paged JSON; `jobDescription` is the full HTML JD."""
    endpoint = spec.get("url") or JOBICY_API
    page_size = max(1, min(bound(spec, "count", 50), JOBICY_PAGE_MAX))
    max_pages = max(1, bound(spec, "max_pages", 1))
    max_items = bound(spec, "max_items", 0)
    name = spec["name"]
    out: list[Job] = []
    cursor = ""
    for _ in range(max_pages):
        payload = _json(fetcher, spec, endpoint + _query({"count": page_size, "cursor": cursor})) or {}
        rows = payload.get("jobs") or []
        for item in rows:
            title = item.get("jobTitle") or ""
            url = item.get("url") or ""
            if not title or not url:
                continue
            out.append(
                Job(
                    company=item.get("companyName") or "",
                    title=title,
                    url=url,
                    source=f"jobicy:{name}",
                    location=item.get("jobGeo") or "",
                    published=_date(item.get("pubDate")),
                    remote=True,
                    description=strip_html(item.get("jobDescription") or item.get("jobExcerpt")),
                    extra={
                        "industry": item.get("jobIndustry"),
                        "level": item.get("jobLevel"),
                        "job_type": item.get("jobType"),
                        "salary_min": item.get("salaryMin"),
                        "salary_max": item.get("salaryMax"),
                        "currency": item.get("salaryCurrency"),
                    },
                )
            )
        cursor = str(payload.get("nextCursor") or "")
        if not rows or not cursor or not payload.get("hasMore"):
            break
        if max_items and len(out) >= max_items:
            break
    return _capped(out, max_items)


def fetch_himalayas(fetcher: Fetcher, spec: dict) -> list[Job]:
    """Offset-paged JSON over 123k rows; `limit` is capped at 20 per page."""
    endpoint = spec.get("url") or HIMALAYAS_API
    page_size = max(1, min(bound(spec, "limit", HIMALAYAS_PAGE_MAX), HIMALAYAS_PAGE_MAX))
    max_pages = max(1, bound(spec, "max_pages", 1))
    max_items = bound(spec, "max_items", 0)
    name = spec["name"]
    out: list[Job] = []
    offset = bound(spec, "offset", 0)
    for _ in range(max_pages):
        payload = _json(fetcher, spec, endpoint + _query({"limit": page_size, "offset": offset})) or {}
        rows = payload.get("jobs") or []
        for item in rows:
            title = item.get("title") or ""
            url = item.get("applicationLink") or item.get("guid") or ""
            if not title or not url:
                continue
            out.append(
                Job(
                    company=item.get("companyName") or "",
                    title=title,
                    url=url,
                    source=f"himalayas:{name}",
                    location=", ".join(item.get("locationRestrictions") or []),
                    published=_date(item.get("pubDate")),
                    remote=True,
                    description=strip_html(item.get("description") or item.get("excerpt")),
                    extra={
                        "seniority": item.get("seniority"),
                        "employment_type": item.get("employmentType"),
                        "categories": item.get("categories"),
                        "timezones": item.get("timezoneRestrictions"),
                    },
                )
            )
        offset += len(rows)
        total = payload.get("totalCount") or 0
        if not rows or (total and offset >= total):
            break
        if max_items and len(out) >= max_items:
            break
    return _capped(out, max_items)


def fetch_workingnomads(fetcher: Fetcher, spec: dict) -> list[Job]:
    """One unpaged array (~52 rows observed); `max_items` is the only bound."""
    endpoint = spec.get("url") or WORKINGNOMADS_API
    max_items = bound(spec, "max_items", 0)
    name = spec["name"]
    payload = _json(fetcher, spec, endpoint)
    out: list[Job] = []
    for item in payload if isinstance(payload, list) else []:
        title = item.get("title") or ""
        url = item.get("url") or ""
        if not title or not url:
            continue
        out.append(
            Job(
                company=item.get("company_name") or "",
                title=title,
                url=url,
                source=f"workingnomads:{name}",
                location=item.get("location") or "",
                published=_date(item.get("pub_date")),
                remote=True,
                description=strip_html(item.get("description")),
                extra={"category": item.get("category_name"), "tags": item.get("tags")},
            )
        )
    return _capped(out, max_items)


def fetch_landingjobs(fetcher: Fetcher, spec: dict) -> list[Job]:
    """Offset-paged JSON array; the JD is `role_description` plus `main_requirements`."""
    endpoint = spec.get("url") or LANDINGJOBS_API
    page_size = bound(spec, "limit", 20)
    max_pages = max(1, bound(spec, "max_pages", 1))
    max_items = bound(spec, "max_items", 0)
    name = spec["name"]
    out: list[Job] = []
    offset = bound(spec, "offset", 0)
    for _ in range(max_pages):
        payload = _json(fetcher, spec, endpoint + _query({"limit": page_size, "offset": offset}))
        rows = payload if isinstance(payload, list) else (payload or {}).get("results") or []
        for item in rows:
            title = item.get("title") or ""
            url = item.get("url") or ""
            if not title or not url:
                continue
            body = " ".join(
                part
                for part in (strip_html(item.get("role_description")), strip_html(item.get("main_requirements")))
                if part
            )
            out.append(
                Job(
                    company=_landing_company(url, title, name),
                    title=title,
                    url=url,
                    source=f"landingjobs:{name}",
                    location=", ".join(
                        ", ".join(str(place.get(part) or "") for part in ("city", "country_code")).strip(", ")
                        for place in item.get("locations") or []
                        if isinstance(place, dict)
                    ),
                    published=_date(item.get("published_at") or item.get("created_at")),
                    remote=bool(item.get("remote")),
                    description=body,
                    extra={
                        "relocation_paid": bool(item.get("relocation_paid")),
                        "tags": item.get("tags"),
                        "salary_min": item.get("gross_salary_low"),
                        "salary_max": item.get("gross_salary_high"),
                        "currency": item.get("currency_code"),
                    },
                )
            )
        offset += len(rows)
        if not rows or len(rows) < page_size:
            break
        if max_items and len(out) >= max_items:
            break
    return _capped(out, max_items)


def fetch_jobdataapi(fetcher: Fetcher, spec: dict) -> list[Job]:
    """DRF-paged, one config entry per country. `language` rides along in `extra`.

    Anonymous requests cannot be paginated: `?page=1` answers 403
    ("Anonymous requests cannot be paginated."), so the parameter is sent only for
    page 2 and up. Page 1 is the anonymous default and is always reachable.
    """
    endpoint = spec.get("url") or JOBDATAAPI_API
    country = str(spec.get("country_code") or "").strip()
    if not country:
        raise ValueError("jobdataapi needs a country_code in the feed spec")
    start = max(1, bound(spec, "page", 1))
    max_pages = max(1, bound(spec, "max_pages", 1))
    max_items = bound(spec, "max_items", 0)
    name = spec["name"]
    out: list[Job] = []
    for page in range(start, start + max_pages):
        url = endpoint + _query({"country_code": country, "page": page if page > 1 else None})
        payload = _json(fetcher, spec, url) or {}
        rows = payload.get("results") or []
        for item in rows:
            title = item.get("title") or ""
            company = item.get("company") or {}
            url_ = item.get("application_url") or ""
            if not title or not url_:
                continue
            out.append(
                Job(
                    company=(company.get("name") if isinstance(company, dict) else "") or "",
                    title=title,
                    url=url_,
                    source=f"jobdataapi:{name}",
                    location=item.get("location") or "",
                    published=_date(item.get("published")),
                    remote=bool(item.get("has_remote")),
                    description=strip_html(item.get("description")),
                    extra={
                        "language": item.get("language"),
                        "experience_level": item.get("experience_level"),
                        "types": [row.get("name") for row in item.get("types") or [] if isinstance(row, dict)],
                        "countries": [
                            row.get("code") for row in item.get("countries") or [] if isinstance(row, dict)
                        ],
                    },
                )
            )
        if not rows or not payload.get("next"):
            break
        if max_items and len(out) >= max_items:
            break
    return _capped(out, max_items)


def fetch_devitjobs(fetcher: Fetcher, spec: dict) -> list[Job]:
    """RSS with the whole JD in `content:encoded`; needs `user_agent: browser`."""
    endpoint = spec.get("url") or DEVITJOBS_RSS
    max_items = bound(spec, "max_items", 0)
    name = spec["name"]
    root = ET.fromstring(fetcher.text(endpoint, headers=headers(spec)))
    feed_title = strip_html(root.findtext(".//channel/title") or name)
    out: list[Job] = []
    for entry in (element for element in root.iter("item")):
        title = strip_html(entry.findtext("title") or "")
        url = _without_tracking((entry.findtext("link") or "").strip())
        if not title or not url:
            continue
        body = entry.findtext(_CONTENT_ENCODED) or entry.findtext("description") or ""
        out.append(
            Job(
                company=company_from_url(url) or guess_company(title, feed_title),
                title=title,
                url=url,
                source=f"devitjobs:{name}",
                location="",
                published=_date(entry.findtext("pubDate") or ""),
                description=strip_html(body),
                extra={"feed": feed_title},
            )
        )
        if max_items and len(out) >= max_items:
            break
    return _capped(out, max_items)


def fetch_themuse(fetcher: Fetcher, spec: dict) -> list[Job]:
    """Page-paged JSON; the whole corpus is 400k+ jobs, so a category keeps it sane.

    Needs `user_agent: browser`: the endpoint answers 403 to an unknown client.
    """
    endpoint = spec.get("url") or THEMUSE_API
    page = max(1, bound(spec, "page", 1))
    max_pages = max(1, bound(spec, "max_pages", 1))
    max_items = bound(spec, "max_items", 0)
    name = spec["name"]
    category = str(spec.get("category") or "")
    out: list[Job] = []
    for _ in range(max_pages):
        payload = _json(fetcher, spec, endpoint + _query({"page": page, "category": category})) or {}
        rows = payload.get("results") or []
        for item in rows:
            title = item.get("name") or ""
            refs = item.get("refs") or {}
            url = (refs.get("landing_page") if isinstance(refs, dict) else "") or ""
            if not title or not url:
                continue
            company = item.get("company") or {}
            location = ", ".join(
                str(place.get("name") or "") for place in item.get("locations") or [] if isinstance(place, dict)
            )
            out.append(
                Job(
                    company=(company.get("name") if isinstance(company, dict) else "") or "",
                    title=title,
                    url=url,
                    source=f"themuse:{name}",
                    location=location,
                    published=_date(item.get("publication_date")),
                    remote=True if "remote" in location.lower() else None,
                    description=strip_html(item.get("contents")),
                    extra={
                        "levels": [row.get("name") for row in item.get("levels") or [] if isinstance(row, dict)],
                        "categories": [
                            row.get("name") for row in item.get("categories") or [] if isinstance(row, dict)
                        ],
                    },
                )
            )
        page += 1
        page_count = payload.get("page_count") or 0
        if not rows or (page_count and page > page_count):
            break
        if max_items and len(out) >= max_items:
            break
    return _capped(out, max_items)


FETCHERS = {
    "jobicy": fetch_jobicy,
    "himalayas": fetch_himalayas,
    "workingnomads": fetch_workingnomads,
    "landingjobs": fetch_landingjobs,
    "jobdataapi": fetch_jobdataapi,
    "devitjobs": fetch_devitjobs,
    "themuse": fetch_themuse,
}
