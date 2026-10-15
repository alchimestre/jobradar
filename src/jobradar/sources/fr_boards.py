"""French many-employer boards: LesJeudis, free-work."""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Callable
from urllib.parse import urlencode

from ..models import Job
from ..net import Fetcher
from .helpers import (
    bound,
    gather,
    guess_company,
    headers,
    job_postings,
    strip_html,
    text_getter,
)


def _source(spec: dict, kind: str) -> str:
    name = str(spec.get("name") or "").strip()
    return f"{kind}:{name}" if name else kind


def _list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _one(value):
    """JSON-LD fields that may hold either one node or a list of them."""
    return value[0] if isinstance(value, list) and value else value


def _text(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    return str(value or "")


def _fold(value: str) -> str:
    """Case- and accent-insensitive form, so `developpeur` matches `développeur`."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", value.lower()) if not unicodedata.combining(ch))


# --------------------------------------------------------------------- LesJeudis
LJ_SITEMAP_URL = "https://lesjeudis.com/sitemap/offre.xml"
LJ_DEFAULT_ITEMS = 25
# `max_items` is a request budget; serial it is also the slowest part of the French lane.
LJ_DETAIL_WORKERS = 6


def _child_text(node: ET.Element, name: str) -> str:
    for child in node:
        if child.tag.split("}")[-1].lower() == name:
            return (child.text or "").strip()
    return ""


def _sitemap_entries(document: str) -> list[tuple[str, str]]:
    """(lastmod, loc) pairs, newest first. One 3.6 MB document, ~18k offers."""
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        return []
    entries: list[tuple[str, str]] = []
    for node in root.iter():
        if node.tag.split("}")[-1].lower() != "url":
            continue
        loc = _child_text(node, "loc")
        if loc:
            entries.append((_child_text(node, "lastmod"), loc))
    entries.sort(key=lambda entry: entry[0], reverse=True)
    return entries


def _place(value) -> str:
    node = _one(value)
    if not isinstance(node, dict):
        return ""
    address = _one(node.get("address"))
    if not isinstance(address, dict):
        return ""
    parts = (address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry"))
    return ", ".join(str(part) for part in parts if part)


def _lj_job(posting: dict, url: str, lastmod: str, source: str) -> Job | None:
    title = str(posting.get("title") or "").strip()
    if not title:
        return None
    organization = _one(posting.get("hiringOrganization"))
    company = str(organization.get("name") or "") if isinstance(organization, dict) else str(organization or "")
    identifier = _one(posting.get("identifier"))
    remote = str(posting.get("jobLocationType") or "").upper()
    return Job(
        company=company.strip() or guess_company(title, "LesJeudis"),
        title=title,
        url=str(posting.get("url") or url),
        source=source,
        location=_place(posting.get("jobLocation")),
        published=str(posting.get("datePosted") or lastmod or "")[:10],
        remote=True if remote == "TELECOMMUTE" else None,
        description=strip_html(posting.get("description") or ""),
        extra={
            "reference": identifier.get("value") if isinstance(identifier, dict) else None,
            "contract": _text(posting.get("employmentType")),
            "valid_through": str(posting.get("validThrough") or "")[:10],
        },
    )


def fetch_lesjeudis(fetcher: Fetcher, spec: dict) -> list[Job]:
    """Sitemap for discovery, the page's JSON-LD `JobPosting` for the payload.

    Discovery is one document; the cost is one page fetch per offer, hence `max_items`.

    Spec: `url` (sitemap override), `max_items`.
    """
    request_headers = headers(spec)
    document = fetcher.text(str(spec.get("url") or LJ_SITEMAP_URL), headers=request_headers)
    limit = max(0, bound(spec, "max_items", LJ_DEFAULT_ITEMS))
    source = _source(spec, "lesjeudis")

    out: list[Job] = []
    entries = _sitemap_entries(document)[:limit]
    pages = gather(
        [text_getter(fetcher, url, request_headers) for _, url in entries], workers=LJ_DETAIL_WORKERS
    )
    for (lastmod, url), page in zip(entries, pages):
        if page is None:
            continue
        postings = job_postings(page)
        if not postings:
            continue
        job = _lj_job(postings[0], url, lastmod, source)
        if job:
            out.append(job)
    return out


# --------------------------------------------------------------------- free-work

FW_API_URL = "https://www.free-work.com/api/job_postings"
FW_JOB_URL = "https://www.free-work.com/fr/tech-it/job-mission/{family}/{slug}"
FW_PAGE_MAX = 100
FW_DEFAULT_ITEMS = 100
_FW_REMOTE = {"full": True, "none": False, "no": False}


def _last_page(payload: dict) -> int:
    """`hydra:view.hydra:last` is a URL; its `page=` is the last page of the collection."""
    view = payload.get("hydra:view") if isinstance(payload, dict) else None
    found = re.search(r"[?&]page=(\d+)", str((view or {}).get("hydra:last") or ""))
    return int(found.group(1)) if found else 0


def _keywords(spec: dict) -> list[str]:
    raw = spec.get("keywords") or spec.get("query") or ""
    items = raw if isinstance(raw, list) else re.split(r"[,\s]+", str(raw))
    return [_fold(str(item).strip()) for item in items if str(item).strip()]


def _fw_keep(row: dict, keywords: list[str], min_rate: int, contracts: set[str]) -> bool:
    if contracts:
        row_contracts = {str(item).strip().lower() for item in _list(row.get("contracts"))}
        if not row_contracts & contracts:
            return False
    if min_rate:
        try:
            if int(row.get("maxDailySalary") or 0) < min_rate:
                return False
        except (TypeError, ValueError):
            return False
    if keywords:
        haystack = _fold(f"{row.get('title') or ''} {strip_html(row.get('description') or '')}")
        if not any(word in haystack for word in keywords):
            return False
    return True


def _fw_job(row: dict, source: str) -> Job | None:
    title = str(row.get("title") or "").strip()
    slug = str(row.get("slug") or "").strip()
    if not title or not slug:
        return None
    job_node = _one(row.get("job"))
    company_node = _one(row.get("company"))
    place_node = _one(row.get("location"))
    family = str(job_node.get("nameForUserSlug") or "") if isinstance(job_node, dict) else ""
    company = str(company_node.get("name") or "") if isinstance(company_node, dict) else ""
    place = place_node if isinstance(place_node, dict) else {}
    return Job(
        company=company.strip() or guess_company(title, "free-work"),
        title=title,
        url=FW_JOB_URL.format(family=family or "tech-it", slug=slug),
        source=source,
        location=str(place.get("label") or place.get("locality") or ""),
        published=str(row.get("publishedAt") or row.get("createdAt") or "")[:10],
        remote=_FW_REMOTE.get(str(row.get("remoteMode") or "").strip().lower()),
        description=strip_html(row.get("description") or ""),
        extra={
            "id": row.get("id"),
            "minDailySalary": row.get("minDailySalary"),
            "maxDailySalary": row.get("maxDailySalary"),
            "dailySalary": row.get("dailySalary"),
            "currency": row.get("currency"),
            "contracts": row.get("contracts"),
            "remote_mode": row.get("remoteMode"),
            "experience": row.get("experienceLevel"),
            "company_slug": company_node.get("slug") if isinstance(company_node, dict) else None,
        },
    )


def fetch_free_work(fetcher: Fetcher, spec: dict) -> list[Job]:
    """Hydra collection of tech missions; targeting is applied over the fetched page.

    Only `page` and `itemsPerPage` are honoured as documented: `q`, `title` and
    `skills.name` all return the unfiltered total, so the spec's keywords, rate and
    contract filters run here instead of in the query string.

    Spec: `items_per_page`, `max_pages`, `max_items`, `keywords`, `min_daily_rate`,
    `contracts`.
    """
    request_headers = headers(spec)
    page_size = min(max(bound(spec, "items_per_page", FW_PAGE_MAX), 1), FW_PAGE_MAX)
    max_pages = max(1, bound(spec, "max_pages", 3))
    cap = bound(spec, "max_items", FW_DEFAULT_ITEMS) or page_size * max_pages
    keywords = _keywords(spec)
    min_rate = bound(spec, "min_daily_rate", 0)
    contracts = {str(item).strip().lower() for item in _list(spec.get("contracts")) if str(item).strip()}
    source = _source(spec, "free_work")

    out: list[Job] = []
    for page in range(1, max_pages + 1):
        url = f"{FW_API_URL}?{urlencode({'page': page, 'itemsPerPage': page_size})}"
        payload = fetcher.json(url, headers=request_headers) or {}
        members = payload.get("hydra:member") if isinstance(payload, dict) else None
        if not members:
            break
        for row in members:
            if not isinstance(row, dict) or not _fw_keep(row, keywords, min_rate, contracts):
                continue
            job = _fw_job(row, source)
            if job:
                out.append(job)
            if len(out) >= cap:
                break
        last = _last_page(payload) if isinstance(payload, dict) else 0
        if len(out) >= cap or page >= last:
            break
    return out[:cap]


FETCHERS: dict[str, Callable[[Fetcher, dict], list[Job]]] = {
    "lesjeudis": fetch_lesjeudis,
    "free_work": fetch_free_work,
}
