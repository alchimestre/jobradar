"""Job record + identity rules."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

_WS = re.compile(r"\s+")


def norm(value: str | None) -> str:
    return _WS.sub(" ", (value or "").strip())


@dataclass(slots=True)
class Job:
    """One posting, normalised across every source.

    Identity is (company, title, location): the same requisition scraped from an
    aggregator and later from the employer's ATS collapses to a single row.
    """

    company: str
    title: str
    url: str
    source: str
    location: str = ""
    published: str = ""
    remote: bool | None = None
    description: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.company = norm(self.company)
        self.title = norm(self.title)
        self.location = norm(self.location)
        self.url = (self.url or "").strip()

    @property
    def uid(self) -> str:
        """Identity is (company, title): the same requisition appears on the employer's
        ATS and on aggregators, with per-board location formatting differences."""
        key = f"{self.company.lower()}|{self.title.lower()}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    def as_row(self) -> tuple:
        return (
            self.uid,
            self.company,
            self.title,
            self.location,
            self.url,
            self.source,
            self.published,
            -1 if self.remote is None else int(self.remote),
            self.description[:4000],
        )


@dataclass(slots=True)
class Match:
    """Result of scoring a posting against the search filters."""

    score: int
    decision: str  # high | review | low | out
    lane: str  # eu-remote | de | ch | fr | nl | au | other
    reasons: list[str] = field(default_factory=list)
    scope: str = "ok"  # ok | restricted  (remote scope excludes the target region)
    region: str = "unknown"  # eu | emea | au | americas | apac | unknown
    language: str = "unknown"  # en | fr | de | unknown
    mode: str = "unknown"  # remote | hybrid | onsite | unknown
    country: str = "unknown"  # fr | de | ch | nl | other | unknown

    @property
    def rank(self) -> int:
        return {"high": 2, "review": 1, "low": 0, "out": 0}[self.decision]
