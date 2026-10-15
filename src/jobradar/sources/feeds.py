"""Aggregator adapters: RSS/Atom, RemoteOK, Arbeitnow, HN "Who is hiring"."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from ..models import Job
from ..net import Fetcher
from .helpers import company_from_url, enrich, guess_company, strip_html


def _first(element: ET.Element, names: tuple[str, ...]) -> str:
    for child in element:
        if child.tag.split("}")[-1].lower() in names:
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def _rss_date(value: str) -> str:
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    found = re.match(r"(\d{4}-\d{2}-\d{2})", value or "")
    return found.group(1) if found else ""


# The who-is-hiring thread attracts two kinds of non-offers: candidates pasting the
# "who wants to be hired?" profile, and comments that are not formatted as a posting at
# all. Both used to become rows whose *title* was the entire comment text.
HIRING_POST_MAX = 160
CANDIDATE_POST_RE = re.compile(
    r"willing to relocate|#?open to work|seeking (?:a |new )?(?:job|role|position|opportunit)|"
    r"looking for (?:a |new )?(?:job|role|position|opportunit)",
    re.I,
)


def fetch_rss(fetcher: Fetcher, url: str, name: str, company: str = "") -> list[Job]:
    root = ET.fromstring(fetcher.text(url))
    feed_title = strip_html(root.findtext(".//channel/title") or root.findtext(".//{http://www.w3.org/2005/Atom}title") or name)
    entries = [e for e in root.iter() if e.tag.split("}")[-1].lower() in ("item", "entry")]
    out = []
    for entry in entries:
        title = strip_html(_first(entry, ("title",)))
        link = _first(entry, ("link",))
        if not link:
            for child in entry:
                if child.tag.split("}")[-1].lower() == "link":
                    link = child.get("href") or ""
                    if link:
                        break
        if not title or not link:
            continue
        published = _rss_date(_first(entry, ("pubdate", "published", "updated", "date")))
        summary = strip_html(_first(entry, ("description", "summary", "content")))
        out.append(
            Job(
                company=company or company_from_url(link) or guess_company(title, feed_title),
                title=title,
                url=link,
                source=f"rss:{name}",
                location=strip_html(_first(entry, ("location", "region", "category"))),
                published=published,
                description=summary,
                extra={"feed": feed_title},
            )
        )
    return out


def fetch_remoteok(fetcher: Fetcher, url: str, name: str) -> list[Job]:
    payload = fetcher.json(url)
    out = []
    for item in payload:
        if not isinstance(item, dict) or not item.get("position"):
            continue
        out.append(
            Job(
                company=item.get("company") or "",
                title=item.get("position") or "",
                url=item.get("url") or item.get("apply_url") or f"https://remoteok.com/remote-jobs/{item.get('id')}",
                source=f"remoteok:{name}",
                location=item.get("location") or "Remote",
                published=str(item.get("date") or "")[:10],
                remote=True,
                description=strip_html(item.get("description") or ""),
                extra={"tags": item.get("tags")},
            )
        )
    return out


def fetch_arbeitnow(fetcher: Fetcher, url: str, name: str) -> list[Job]:
    payload = fetcher.json(url)
    out = []
    for item in payload.get("data") or []:
        description = strip_html(item.get("description") or "")
        out.append(
            Job(
                company=item.get("company_name") or "",
                title=item.get("title") or "",
                url=item.get("url") or "",
                source=f"arbeitnow:{name}",
                location=item.get("location") or "",
                published=datetime.fromtimestamp(
                    item.get("created_at", 0), tz=timezone.utc
                ).strftime("%Y-%m-%d") if item.get("created_at") else "",
                remote=bool(item.get("remote")),
                description=description,
                extra={"tags": item.get("tags"), "job_types": item.get("job_types")},
            )
        )
    return out


def fetch_hn_whoishiring(fetcher: Fetcher, url: str, name: str) -> list[Job]:
    """Latest 'Ask HN: Who is hiring?' thread, one Job per top-level comment."""
    hits = fetcher.json(url).get("hits") or []
    thread = next((h for h in hits if "who is hiring" in (h.get("title") or "").lower()), None)
    if thread is None:
        raise ValueError("no 'Who is hiring' thread found in the latest submissions")
    item = fetcher.json(f"https://hn.algolia.com/api/v1/items/{thread['objectID']}")
    out = []
    for child in item.get("children") or []:
        raw = strip_html(child.get("text") or "")
        if not raw:
            continue
        # A candidate's own profile is not an offer (HN id 49686668 was one).
        if CANDIDATE_POST_RE.search(raw):
            continue
        parts = [p.strip() for p in raw.split("|")]
        # The thread format is "Company | Role | Location | …"; anything without a
        # separator is prose, and a row built from it would carry the whole comment as
        # its title.
        if len(parts) < 2 or not parts[1]:
            continue
        company, title, location = parts[0], parts[1][:HIRING_POST_MAX], parts[2] if len(parts) > 2 else ""
        company = re.sub(r"https?://\S+", "", company).strip(" -–—()")[:80]
        if not title:
            continue
        out.append(
            Job(
                company=company or "HN",
                title=title,
                url=f"https://news.ycombinator.com/item?id={child.get('id')}",
                source=f"hn:{name}",
                location=location[:120],
                published=(child.get("created_at") or "")[:10],
                description=raw[:2000],
                remote="remote" in location.lower(),
                extra={"thread": thread.get("title")},
            )
        )
    return out


FEED_FETCHERS = {
    "rss": lambda fetcher, spec: enrich(
        fetcher, fetch_rss(fetcher, spec["url"], spec["name"], spec.get("company", "")), spec
    ),
    "remoteok": lambda fetcher, spec: fetch_remoteok(fetcher, spec["url"], spec["name"]),
    "arbeitnow": lambda fetcher, spec: fetch_arbeitnow(fetcher, spec["url"], spec["name"]),
    "hn": lambda fetcher, spec: fetch_hn_whoishiring(fetcher, spec["url"], spec["name"]),
}
