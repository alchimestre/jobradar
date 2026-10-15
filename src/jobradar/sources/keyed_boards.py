"""Boards that need a key or a bespoke parse: web3.career, Lobsters.

web3.career carries its credential in the query string. Lobsters needs none, but its
postings are free-text comments inside a quarterly thread.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

from ..models import Job
from ..net import Fetcher
from .helpers import bound, credential, guess_company, headers, strip_html

_REMOTE_RE = re.compile(
    r"\b(remote|fully[- ]remote|t[ée]l[ée]travail|home[- ]office|work from home)\b", re.I
)


def _remote_from_text(text: str) -> bool | None:
    """No source here publishes a remote flag: the board text is the only evidence."""
    return True if _REMOTE_RE.search(text or "") else None


# ------------------------------------------------------------- web3.career

# No cursor and a hard cap of 100 rows per request, so `limit` is the whole pager.
WEB3_MAX_ITEMS = 100


def _web3_rows(payload) -> list[dict]:
    """Locate the job array in the documented `[string, string, Job[]]` mixed root.

    [INFERENCE] Everything about the live payload: every request without a valid token
    302s to `https://web3.career/web3-jobs-api`, so the shape and the description toggle
    come from the docs at https://docs.bondex.app/api-reference/web3-career-jobs-api,
    not from an observed response. Hence no index is assumed and a dict-wrapped or flat
    array is accepted too.
    """
    if isinstance(payload, dict):
        for key in ("jobs", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
                return value
        return []
    if not isinstance(payload, list):
        return []
    for element in payload:
        if isinstance(element, list) and element and all(isinstance(v, dict) for v in element):
            return element
    if payload and all(isinstance(v, dict) for v in payload):
        return payload
    return []


def _pick(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _boolish(value) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in ("1", "true", "yes", "remote"):
        return True
    if text in ("0", "false", "no", "onsite", "on-site"):
        return False
    return None


def fetch_web3career(fetcher: Fetcher, spec: dict) -> list[Job]:
    token = credential(spec, "token", "WEB3CAREER_KEY")
    name = str(spec.get("name") or "web3career")
    max_items = max(0, bound(spec, "max_items", WEB3_MAX_ITEMS))
    limit = min(max(0, bound(spec, "limit", WEB3_MAX_ITEMS)), WEB3_MAX_ITEMS, max_items)

    params = {"token": token, "limit": limit}
    for key in ("tag", "remote", "country"):
        if spec.get(key) not in (None, ""):
            params[key] = str(spec[key])

    payload = fetcher.json(f"https://web3.career/api/v1?{urlencode(params)}", headers=headers(spec))
    out: list[Job] = []
    for row in _web3_rows(payload):
        title = _pick(row, "position", "title", "name", "role")
        if not title:
            continue
        # Attribution terms require the `apply_url` link, so it wins over a board `url`.
        apply_url = _pick(row, "apply_url", "url", "link", "apply_link")
        location = _pick(row, "location", "country", "city")
        remote = _boolish(row.get("remote"))
        published = _pick(row, "date", "created_at", "published_at", "posted_at")
        out.append(
            Job(
                company=_pick(row, "company", "company_name", "organization", "org", "employer")
                or guess_company(title, "web3.career"),
                title=title,
                url=apply_url,
                source=f"web3career:{name}",
                location=location,
                published=published[:10],
                remote=remote if remote is not None else _remote_from_text(f"{title} {location}"),
                description=strip_html(_pick(row, "description", "description_text", "job_description")),
                extra={"tags": row.get("tags"), "apply_url": apply_url},
            )
        )
        if len(out) >= max_items:
            break
    return out


# ---------------------------------------------------------------- lobsters

LOBSTERS_FEED = "https://lobste.rs/t/job.rss"
HIRING_THREAD_RE = re.compile(r"who(?:'|\u2019)?s hiring|who is hiring", re.I)
SHORT_ID_RE = re.compile(r"/s/([A-Za-z0-9]+)")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
LABEL_RE = re.compile(r"^([A-Za-z][A-Za-z ()/&.'-]{1,30}?)\s*:\s*(.+)$")
TRAILING_GROUP_RE = re.compile(r"\s*[\[(][^\])]*[\])]\s*$")

TITLE_LABELS = ("positions", "position", "roles", "role", "title", "jobtitle")
COMPANY_LABELS = ("company", "employer", "organization", "organisation", "org", "companyname")
LOCATION_LABELS = ("location", "locations", "where", "city", "region")

LOBSTERS_POST_MAX = 160
LOBSTERS_DEFAULT_ITEMS = 200


def _plain(markdown: str) -> str:
    """Lobsters serves markdown: `**Company:** [Acme](https://acme.test)` -> `Company: Acme`.

    Only the `**` bold form is unwrapped; single underscores stay, so identifiers like
    `__init__` in a posting survive.
    """
    text = MARKDOWN_LINK_RE.sub(r"\1", markdown or "")
    return text.replace("**", "")


def _labels(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip().lstrip("> ").strip()
        if not line or "://" in line:
            continue
        found = LABEL_RE.match(line)
        if not found:
            continue
        out.setdefault(re.sub(r"[^a-z]", "", found.group(1).lower()), found.group(2).strip())
    return out


def _first_field(labels: dict[str, str], keys: tuple[str, ...]) -> str:
    return next((labels[key] for key in keys if labels.get(key)), "")


def _threads(feed: str) -> list[tuple[str, str]]:
    root = ET.fromstring(feed)
    out: list[tuple[str, str]] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("guid") or "").strip() or (item.findtext("link") or "").strip()
        if title and link:
            out.append((title, link))
    return out


def fetch_lobsters(fetcher: Fetcher, spec: dict) -> list[Job]:
    """The quarterly `job`-tagged who's-hiring thread, one Job per top-level comment."""
    name = str(spec.get("name") or "lobsters")
    request_headers = headers(spec)
    max_items = max(0, bound(spec, "max_items", LOBSTERS_DEFAULT_ITEMS))

    feed = fetcher.text(str(spec.get("url") or LOBSTERS_FEED), headers=request_headers)
    thread_title, thread_link = "", ""
    for title, link in _threads(feed):
        if HIRING_THREAD_RE.search(title):
            thread_title, thread_link = title, link
            break
    if not thread_link:
        raise ValueError(f"no who's-hiring thread in {spec.get('url') or LOBSTERS_FEED}")
    short = SHORT_ID_RE.search(thread_link)
    if short is None:
        raise ValueError(f"unrecognised Lobsters thread url: {thread_link}")

    thread = fetcher.json(f"https://lobste.rs/s/{short.group(1)}.json", headers=request_headers) or {}
    out: list[Job] = []
    for comment in thread.get("comments") or []:
        if not isinstance(comment, dict):
            continue
        # Only the top-level comments are postings; everything below them is discussion.
        if comment.get("depth") or comment.get("is_deleted") or comment.get("is_moderated"):
            continue
        raw = _plain(str(comment.get("comment_plain") or ""))
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            continue
        labels = _labels(raw)
        first_line = lines[0]
        title = _first_field(labels, TITLE_LABELS) or first_line
        company = TRAILING_GROUP_RE.sub("", _first_field(labels, COMPANY_LABELS)).strip(" -–—*_")
        if not company:
            company = guess_company(title, "Lobsters")
        body = re.sub(r"\s+", " ", raw).strip()
        out.append(
            Job(
                company=company,
                title=title[:LOBSTERS_POST_MAX],
                url=str(comment.get("short_id_url") or thread_link),
                source=f"lobsters:{name}",
                location=_first_field(labels, LOCATION_LABELS)[:120],
                published=str(comment.get("created_at") or "")[:10],
                remote=_remote_from_text(body),
                description=body[:4000],
                extra={"thread": thread_title},
            )
        )
        if len(out) >= max_items:
            break
    return out


# ------------------------------------------------------------------ registry

FETCHERS = {
    "web3career": fetch_web3career,
    "lobsters": fetch_lobsters,
}
