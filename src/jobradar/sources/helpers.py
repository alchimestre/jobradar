"""Shared plumbing for source adapters: HTML text, credentials, bounds, enrichment."""

from __future__ import annotations

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

# Several boards reject an unknown client outright: DevITjobs answers 499 to anything
# that is not a browser, The Muse answers 403. Sources that need it set `user_agent`.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def strip_html(value: str | None) -> str:
    """Plain text from a payload that may be HTML, or HTML that has been escaped twice.

    Greenhouse `content` and every RSS `description` arrive as `&lt;p&gt;…`, so unescaping
    has to happen before tags are stripped — the other order leaves the markup in the text.
    """
    return _WS.sub(" ", _TAG.sub(" ", html.unescape(value or ""))).strip()


def guess_company(title: str, fallback: str) -> str:
    """Feeds rarely carry a company field; recover it from the posting title."""
    # Boards suffix salary/benefit brackets ("... @ Dialectic [CHF 110'000 - 150'000]").
    # Only *trailing* groups are stripped: "(Remote) at Kraken" must keep its employer.
    head = re.sub(r"(\s*[\[(][^\])]*[\])])+\s*$", "", title or "").strip() or (title or "")
    for pattern in (r"^(?P<c>[^:]{2,40}?)\s*[:—–-]\s*\S", r"\s+(?:at|@)\s+(?P<c>[^|,()]{2,40})$"):
        found = re.search(pattern, head)
        if found:
            candidate = found.group("c").strip(" -–—")
            if 2 <= len(candidate) <= 40 and not any(ch.isdigit() for ch in candidate):
                return candidate
    return fallback


_SLUG_COMPANY = re.compile(r"-at-([a-z0-9][a-z0-9-]{1,60})/?$", re.I)
_SLUG_NOISE = {"chf", "eur", "usd", "gbp", "salaries", "salary", "k"}


def _trim_slug_tail(slug: str) -> str:
    """`dialectic-chf-110-000` -> `dialectic`: stop at the first numeric/currency token."""
    keep: list[str] = []
    for token in slug.split("-"):
        if token.isdigit() or token.lower() in _SLUG_NOISE:
            break
        keep.append(token)
    return "-".join(keep)


def company_from_url(url: str) -> str:
    """Boards like cryptojobslist encode the employer in the URL slug (`...-at-chainlink`)."""
    path = (url or "").split("?")[0].rstrip("/")
    found = _SLUG_COMPANY.search(path)
    if not found:
        return ""
    slug = _trim_slug_tail(found.group(1)) or found.group(1)
    return slug.replace("-", " ").title()


# ---------------------------------------------------------------- credentials


class MissingCredential(Exception):
    """A source needs a key that is not configured. Raised, never guessed."""


def credential(spec: dict, key: str, env: str) -> str:
    value = str(spec.get(key) or "")
    if not value:
        raise MissingCredential(f"{key} (set {env})")
    return value


def headers(spec: dict) -> dict | None:
    """Per-source request headers. `user_agent: browser` selects the browser UA."""
    out: dict[str, str] = {}
    ua = str(spec.get("user_agent") or "")
    if ua:
        out["User-Agent"] = BROWSER_UA if ua == "browser" else ua
    out.update(spec.get("headers") or {})
    return out or None


# ---------------------------------------------------------------- bounds


def bound(spec: dict, key: str, default: int) -> int:
    try:
        return int(spec.get(key, default))
    except (TypeError, ValueError):
        return default


def gather(thunks: list, workers: int = 6) -> list:
    """Run independent network calls at once, returning results in input order.

    A failed call yields None. A scan is dominated by these calls — one Workday board is up
    to 200 page fetches, one sitemap feed is 40 — so serialising them makes the whole run as
    slow as its slowest source.
    """
    if not thunks:
        return []
    if workers <= 1 or len(thunks) == 1:
        return [_or_none(thunk) for thunk in thunks]
    with ThreadPoolExecutor(max_workers=min(workers, len(thunks))) as pool:
        return list(pool.map(_or_none, thunks))


def _or_none(thunk):
    try:
        return thunk()
    except Exception:
        return None


def text_getter(fetcher, url: str, request_headers: dict | None = None):
    return lambda: fetcher.text(url, headers=request_headers)


# ---------------------------------------------------------------- HTML payloads

_LD_BLOCK = re.compile(
    r"<script[^>]*application/ld(?:\+|&#x2?B;|\\u002[bB])json[^>]*>(.*?)</script>",
    re.I | re.S,
)


def job_postings(html_text: str) -> list[dict]:
    """schema.org JobPosting objects embedded in a page.

    HelloWork escapes the `+` in `application/ld+json` as `&#x2B;`, which a plain
    `application/ld+json` search misses entirely.
    """
    out: list[dict] = []
    for block in _LD_BLOCK.findall(html_text or ""):
        try:
            payload = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        for node in payload if isinstance(payload, list) else [payload]:
            if not isinstance(node, dict):
                continue
            for candidate in [node, *node.get("@graph", [])]:
                if not isinstance(candidate, dict):
                    continue
                kind = candidate.get("@type")
                kinds = kind if isinstance(kind, list) else [kind]
                if "JobPosting" in kinds:
                    out.append(candidate)
    return out


def description_from_page(page: str) -> str:
    """Preferred JD text from a posting page: the JSON-LD description, else the page text."""
    for posting in job_postings(page):
        body = strip_html(str(posting.get("description") or ""))
        if len(body) >= 120:
            return body
    return strip_html(page)


def enrich(fetcher, jobs: list, spec: dict, limit: int = 0) -> list:
    """Replace teaser descriptions with the posting's real text, for a bounded subset.

    Feeds that carry only a snippet ("Acme is looking to hire a ...") leave jobradar's
    JD-language gate with nothing to judge, and an undetected language is out of scope.
    Fetching the posting page restores the gate. Failures leave the row as it was: an
    unreachable page must not cost the whole source.
    """
    budget = limit or bound(spec, "enrich_limit", 0)
    if budget <= 0:
        return jobs
    request_headers = headers(spec)
    todo: list = []
    for job in jobs:
        if len(todo) >= budget:
            break
        if len(job.description or "") >= 120:
            continue
        todo.append(job)
    pages = gather([text_getter(fetcher, job.url, request_headers) for job in todo])
    for job, page in zip(todo, pages):
        if page is None:
            continue
        body = description_from_page(page)
        if len(body) > len(job.description or ""):
            job.description = body[:4000]
    return jobs
