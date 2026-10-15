"""Employer-ATS adapters. Every endpoint here is public and unauthenticated."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from ..models import Job
from ..net import Fetcher
from .helpers import strip_html


def _date(value) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    found = re.match(r"(\d{4}-\d{2}-\d{2})", str(value))
    return found.group(1) if found else ""


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("location_str") or "")
    return str(value or "")


def fetch_ashby(fetcher: Fetcher, token: str, company: str) -> list[Job]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"
    payload = fetcher.json(url)
    jobs = payload.get("jobs") or []
    out = []
    for item in jobs:
        if item.get("isListed") is False:
            continue
        out.append(
            Job(
                company=company,
                title=item.get("title", ""),
                url=item.get("jobUrl") or item.get("applyUrl") or "",
                source=f"ashby:{token}",
                location=item.get("location") or "",
                published=_date(item.get("publishedAt")),
                remote=item.get("isRemote"),
                description=item.get("descriptionPlain") or "",
                extra={"department": item.get("department"), "employmentType": item.get("employmentType")},
            )
        )
    return out


def fetch_greenhouse(fetcher: Fetcher, token: str, company: str) -> list[Job]:
    # `content=true` is one request for the whole board and the only way to see the JD.
    # Without it every posting reaches the language gate with an empty description and is
    # stored out of scope, which is what happened to 130 of the 147 greenhouse rows held.
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    payload = fetcher.json(url)
    out = []
    for item in payload.get("jobs") or []:
        out.append(
            Job(
                company=company,
                title=item.get("title", ""),
                url=item.get("absolute_url") or "",
                source=f"greenhouse:{token}",
                location=_text(item.get("location")),
                published=_date(item.get("first_published") or item.get("updated_at")),
                description=strip_html(item.get("content") or ""),
                extra={"department": (item.get("metadata") or [{}])[0].get("value") if item.get("metadata") else None},
            )
        )
    return out


def fetch_lever(fetcher: Fetcher, token: str, company: str) -> list[Job]:
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    payload = fetcher.json(url)
    if not isinstance(payload, list):
        raise ValueError(f"lever board '{token}' did not return a posting list")
    out = []
    for item in payload:
        categories = item.get("categories") or {}
        out.append(
            Job(
                company=company,
                title=item.get("text", ""),
                url=item.get("hostedUrl") or item.get("applyUrl") or "",
                source=f"lever:{token}",
                location=_text(categories.get("location")),
                published=_date(item.get("createdAt")),
                remote=(str(categories.get("commitment", "")).lower() == "remote" or "remote" in str(categories.get("location", "")).lower()),
                description=item.get("descriptionPlain") or item.get("description") or "",
                extra={"team": categories.get("team"), "commitment": categories.get("commitment")},
            )
        )
    return out


def _workable_location(item: dict) -> str:
    """Geography from the widget payload's `locations[]`.

    The payload carries no `location` key, so the old `item.get("location")` read always
    produced "" — every Workable posting stored `country: unknown` and was tagged
    `-geo-unverified` despite the answer being right there. A multi-country requisition
    carries one entry per country, so they are joined; the rest of the fields are
    fallbacks for payloads that only carry the flat country/city pair.
    """
    if item.get("location"):
        return _text(item["location"])
    labels: list[str] = []
    for loc in item.get("locations") or []:
        if isinstance(loc, dict):
            parts = (_text(loc.get("city")), _text(loc.get("country") or loc.get("countryCode")))
        else:
            parts = (_text(loc), "")
        label = ", ".join(p for p in parts if p)
        if label and label not in labels:
            labels.append(label)
    if labels:
        return "; ".join(labels)
    return ", ".join(p for p in (_text(item.get("city")), _text(item.get("country")), _text(item.get("state"))) if p)


def fetch_workable(fetcher: Fetcher, token: str, company: str) -> list[Job]:
    url = f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"
    payload = fetcher.json(url)
    name = payload.get("name") or company
    out = []
    for item in payload.get("jobs") or []:
        location = _workable_location(item)
        shortcode = item.get("shortcode") or item.get("code") or ""
        out.append(
            Job(
                company=name,
                title=item.get("title") or item.get("name") or "",
                url=item.get("url") or (f"https://apply.workable.com/{token}/j/{shortcode}/" if shortcode else ""),
                source=f"workable:{token}",
                location=location,
                published=_date(item.get("published_on") or item.get("created_at")),
                remote=item.get("telecommuting") if "telecommuting" in item else ("remote" in location.lower() or None),
                description=item.get("description") or "",
                extra={"department": item.get("department"), "employment_type": item.get("employment_type")},
            )
        )
    return out


def fetch_smartrecruiters(fetcher: Fetcher, token: str, company: str) -> list[Job]:
    url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    payload = fetcher.json(url)
    out = []
    for item in payload.get("content") or []:
        location = item.get("location") or {}
        pretty = ", ".join(str(location.get(key)) for key in ("city", "region", "country") if location.get(key))
        job_id = item.get("id", "")
        out.append(
            Job(
                company=company,
                title=item.get("name", ""),
                url=f"https://jobs.smartrecruiters.com/{token}/{job_id}",
                source=f"smartrecruiters:{token}",
                location=pretty,
                published=_date(item.get("releasedDate")),
                remote=bool(location.get("remote")) or None,
                description="",
                extra={"department": (item.get("department") or {}).get("label")},
            )
        )
    return out


def fetch_personio(fetcher: Fetcher, token: str, company: str) -> list[Job]:
    root = ET.fromstring(fetcher.text(f"https://{token}.jobs.personio.de/xml?language=en"))
    out = []
    for position in root.iter("position"):
        job_id = (position.findtext("id") or "").strip()
        offices = [position.findtext("office") or ""]
        offices += [node.text or "" for node in position.findall("./additionalOffices/office")]
        # Personio carries the full JD text: needed for JD-language detection on DACH boards.
        # Only <value> holds prose; <name> is the section heading ("Intro", "Your tasks").
        descriptions = position.find("jobDescriptions")
        body = (
            strip_html(" ".join("".join(value.itertext()) for value in descriptions.iter("value")))
            if descriptions is not None
            else ""
        )
        out.append(
            Job(
                company=company,
                title=(position.findtext("name") or "").strip(),
                url=f"https://{token}.jobs.personio.de/job/{job_id}",
                source=f"personio:{token}",
                location=", ".join(o.strip() for o in offices if o and o.strip()),
                published=_date(position.findtext("createdAt")),
                remote=None,
                description=body,
            )
        )
    return out


def fetch_recruitee(fetcher: Fetcher, token: str, company: str) -> list[Job]:
    payload = fetcher.json(f"https://{token}.recruitee.com/api/offers/")
    out = []
    for item in payload.get("offers") or []:
        out.append(
            Job(
                company=company,
                title=item.get("title", ""),
                url=item.get("careers_url") or item.get("url") or "",
                source=f"recruitee:{token}",
                location=", ".join(str(item.get(k)) for k in ("city", "country") if item.get(k)),
                published=_date(item.get("published_at")),
                remote=item.get("remote"),
                description=item.get("description") or "",
            )
        )
    return out


FETCHERS = {
    "ashby": fetch_ashby,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "workable": fetch_workable,
    "smartrecruiters": fetch_smartrecruiters,
    "personio": fetch_personio,
    "recruitee": fetch_recruitee,
}

ATS_NAMES = tuple(FETCHERS)
