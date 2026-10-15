"""Turn a company name or a job URL into a verified (ats, token) pair.

Two entry points:
  learn_from_url(url)  — zero cost, used on every ingested aggregator hit
  probe(name)          — network probing, used by `jobradar discover`
"""

from __future__ import annotations

import re
import time

from .net import Fetcher
from .sources import FETCHERS

# Ordered: the first pattern that matches wins. A third element is a token template:
# platforms whose token is not one URL segment (Workday needs `<host>/<site>`) name the
# groups to join, otherwise the token is group `t`.
URL_PATTERNS: list[tuple[str, str] | tuple[str, str, str]] = [
    (r"jobs\.ashbyhq\.com/(?P<t>[A-Za-z0-9._-]+)", "ashby"),
    (r"api\.ashbyhq\.com/posting-api/job-board/(?P<t>[A-Za-z0-9._-]+)", "ashby"),
    (r"(?:job-boards|boards)\.greenhouse\.io/(?P<t>[A-Za-z0-9._-]+)", "greenhouse"),
    (r"jobs\.lever\.co/(?P<t>[A-Za-z0-9._-]+)", "lever"),
    # `/j/<id>` is Workable's job path, not an account: it must not become the token.
    (r"apply\.workable\.com/(?!j/)(?P<t>[A-Za-z0-9._-]+)", "workable"),
    (r"(?P<t>[A-Za-z0-9-]+)\.workable\.com", "workable"),
    (r"(?:jobs|careers)\.smartrecruiters\.com/(?P<t>[A-Za-z0-9._-]+)", "smartrecruiters"),
    (r"api\.smartrecruiters\.com/v1/companies/(?P<t>[A-Za-z0-9._-]+)", "smartrecruiters"),
    (r"(?P<t>[A-Za-z0-9-]+)\.jobs\.personio\.de", "personio"),
    (r"(?P<t>[A-Za-z0-9-]+)\.jobs\.personio\.com", "personio"),
    (r"(?P<t>[A-Za-z0-9-]+)\.recruitee\.com", "recruitee"),
    # The API form first: its `wday/cxs` segment would otherwise be read as the career site.
    (
        r"(?P<t>[A-Za-z0-9-]+\.wd\d+)\.myworkdayjobs\.com/wday/cxs/[^/]+/(?P<s>[A-Za-z0-9._-]+)",
        "workday",
        "{t}/{s}",
    ),
    (
        r"(?P<t>[A-Za-z0-9-]+\.wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?(?P<s>[A-Za-z0-9._-]+)",
        "workday",
        "{t}/{s}",
    ),
    (r"(?P<t>[A-Za-z0-9-]+)\.career\.softgarden\.de", "softgarden"),
]

STOP_TOKENS = {"www", "jobs", "careers", "job", "apply", "boards", "job-boards", "en", "home"}


def learn_from_url(url: str) -> tuple[str, str] | None:
    """Extract (ats, token) from any known ATS URL. No network call."""
    for entry in URL_PATTERNS:
        pattern, ats = entry[0], entry[1]
        template = entry[2] if len(entry) > 2 else "{t}"
        found = re.search(pattern, url or "", re.I)
        if not found:
            continue
        try:
            token = template.format(**found.groupdict())
        except (KeyError, IndexError):
            continue
        if token and token.lower() not in STOP_TOKENS:
            return ats, token
    return None


def variants(name: str) -> list[str]:
    """Token guesses for a company name, most specific first.

    ATS slugs are case-sensitive (Ashby `vClusterLabs`, Greenhouse `BoschGroup`),
    so both the original and the lowercase form are tried.
    """
    cleaned = re.sub(r"[^\w\s-]", "", name).strip()
    base = cleaned.lower()
    squashed = re.sub(r"[\s-]+", "", cleaned)
    dashed = re.sub(r"[\s_]+", "-", cleaned)
    words = [w for w in re.split(r"[\s-]+", cleaned) if w]
    guesses = [squashed, squashed.lower(), dashed, dashed.lower()]
    if words:
        guesses += [words[0], words[0].lower(), "-".join(words[:2]), "-".join(words[:2]).lower()]
    out: list[str] = []
    for guess in guesses:
        if guess and len(guess) > 1 and guess not in out:
            out.append(guess)
    return out[:6]


def board_name(company: str, token: str) -> str:
    """Label a learned board with the employer when it matches the slug, else the slug.

    A job URL can point at another organisation's board (group/subsidiary boards), so
    "Hetzner" + token `xneelo` must be labelled `xneelo`, not `Hetzner`.
    """
    squashed = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    if squashed and (token.lower() in squashed or squashed in token.lower()):
        return company
    return token


def probe(name_or_url: str, fetcher: Fetcher | None = None, ats_filter: tuple[str, ...] = ()) -> list[dict]:
    """Probe public ATS endpoints for a company name or URL.

    Stops at the first verified board: a company practically always uses one ATS,
    and probing the whole matrix invites 429s from the stricter endpoints.
    """
    direct = learn_from_url(name_or_url)
    display = f"{direct[1]}" if direct else name_or_url
    candidates: list[tuple[str, str]] = [direct] if direct else []
    if not direct:
        for token in variants(name_or_url):
            candidates += [(ats, token) for ats in (ats_filter or tuple(FETCHERS))]

    owned = fetcher is None
    fetcher = fetcher or Fetcher()
    tried: set[tuple[str, str]] = set()
    try:
        for ats, token in candidates:
            if (ats, token.lower()) in tried or ats not in FETCHERS:
                continue
            tried.add((ats, token.lower()))
            try:
                jobs = FETCHERS[ats](fetcher, token, display)
            except Exception:
                time.sleep(0.2)  # 429-friendly pacing
                continue
            if not jobs:
                continue
            return [
                {
                    "name": display,
                    "ats": ats,
                    "token": token,
                    "jobs_count": len(jobs),
                    "sample_title": jobs[0].title,
                    "sample_location": jobs[0].location,
                    "verified_url": jobs[0].url,
                }
            ]
    finally:
        if owned:
            fetcher.close()
    return []
