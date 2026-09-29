"""Scoring, lane detection and remote-scope triage. Pure functions, no I/O."""

from __future__ import annotations

import re

from .config import (
    ENGLISH_MARKERS,
    FRENCH_MARKERS,
    GERMAN_MARKERS,
    LANE_RULES,
    REGION_IN_SCOPE,
    REGION_OUT_OF_SCOPE,
    REMOTE_RE,
    SCOPE_BLOCKED_RE,
    SCOPE_OPEN_RE,
    WORLDWIDE_RE,
)
from .models import Job, Match

REMOTE = re.compile(REMOTE_RE, re.I)
SCOPE_BLOCKED = re.compile(SCOPE_BLOCKED_RE, re.I)
SCOPE_OPEN = re.compile(SCOPE_OPEN_RE, re.I)

REGION_IN = [(region, re.compile(p, re.I)) for region, p in REGION_IN_SCOPE]
REGION_OUT = [(region, re.compile(p, re.I)) for region, p in REGION_OUT_OF_SCOPE]
WORLDWIDE = re.compile(WORLDWIDE_RE, re.I)
GERMAN = re.compile(GERMAN_MARKERS, re.I)
FRENCH = re.compile(FRENCH_MARKERS, re.I)
ENGLISH = re.compile(ENGLISH_MARKERS, re.I)
UMLAUT = re.compile(r"[äöüßÄÖÜ]")
FR_ACCENT = re.compile(r"[àâçéèêëîïôùûÿœ]")
GERMAN_TITLE = re.compile(
    r"\b(entwickler|ingenieur|betreuer|mitarbeiter|referent|berater|leiter|softwareentwickler|"
    r"systemadministrator|fachbereich|stellenangebot|werkstudent|praktikant)\b",
    re.I,
)
FRENCH_TITLE = re.compile(
    r"\b(d[eé]veloppeur|ing[eé]nieur|responsable|charg[eé]|alternance|stagiaire|candidat)\b", re.I
)


def _rules(entries) -> list[tuple[re.Pattern, int, str]]:
    return [(re.compile(p, re.I), int(w), t) for p, w, t in entries]


HARD_NEGATIVES = {
    "intern",
    "leadership",
    "non-ic",
    "go-to-market",
    "non-engineering",
    "off-target",
    "seniority-stretch",
}


class Scorer:
    def __init__(
        self,
        title_rules,
        negative_rules,
        high: int = 6,
        review: int = 3,
        allowed_languages: tuple[str, ...] = ("en", "fr", "unknown"),
        allowed_regions: tuple[str, ...] = ("eu", "emea", "au", "unknown"),
    ):
        self.title = _rules(title_rules)
        self.negative = _rules(negative_rules)
        self.high, self.review = high, review
        self.lanes = [(lane, re.compile(p, re.I)) for lane, p in LANE_RULES]
        self.allowed_languages = tuple(allowed_languages)
        self.allowed_regions = tuple(allowed_regions)

    def region(self, job: Job) -> str:
        """eu | emea | au | americas | apac | unknown.

        In-scope patterns are tested first: a posting offering both a US and an EU
        location is applicable, so it stays in scope.
        """
        hay = f"{job.location} {job.title}"
        for region, pattern in REGION_IN:
            if pattern.search(hay):
                return region
        for region, pattern in REGION_OUT:
            if pattern.search(hay):
                return region
        if WORLDWIDE.search(hay):
            return "eu"  # worldwide remote includes the EU
        return "unknown"

    def language(self, job: Job) -> str:
        """en | fr | de | unknown, from the description when there is one.

        German-market postings *written in English* stay in scope: only the JD language
        is judged, never the country.
        """
        title = job.title or ""
        body = (job.description or "")[:1500]
        if len(body) >= 120:
            text = f"{title} {body}"
            german = len(GERMAN.findall(text)) + 3 * len(UMLAUT.findall(text))
            french = len(FRENCH.findall(text)) + 3 * len(FR_ACCENT.findall(text))
            english = len(ENGLISH.findall(text))
            if german >= 4 and german > 1.4 * max(french, english):
                return "de"
            if french >= 4 and french > 1.4 * max(german, english):
                return "fr"
            if english >= 4:
                return "en"
            return "unknown"
        if UMLAUT.search(title) or GERMAN_TITLE.search(title):
            return "de"
        if FR_ACCENT.search(title) or FRENCH_TITLE.search(title):
            return "fr"
        return "unknown"

    def lane(self, job: Job) -> str:
        hay = f"{job.location} {job.title}"
        if REMOTE.search(hay) and not any(p.search(job.location) for _, p in self.lanes):
            return "eu-remote"
        for lane, pattern in self.lanes:
            if pattern.search(hay):
                return lane
        return "other"

    def scope(self, job: Job) -> str:
        """Remote postings that exclude Europe are noise, not opportunities."""
        if not (job.remote or REMOTE.search(job.location)):
            return "ok"
        hay = f"{job.location} {job.title}"
        if SCOPE_OPEN.search(hay):
            return "ok"
        if SCOPE_BLOCKED.search(hay):
            return "restricted"
        return "ok"

    def match(self, job: Job) -> Match:
        hay = f"{job.title} {job.location}"
        score, reasons = 0, []
        strongest_title = 0
        for pattern, weight, tag in self.title:
            if pattern.search(hay):
                score += weight
                reasons.append(f"+{tag}")
                if weight > 0:
                    strongest_title = max(strongest_title, weight)
        for pattern, weight, tag in self.negative:
            if pattern.search(hay):
                reasons.append(f"-{tag}")
                # A soft negative (e.g. "data engineer") is overruled by a strong
                # target-title signal; a hard one (seniority, leadership, GTM) always bites.
                if tag in HARD_NEGATIVES or abs(weight) > strongest_title:
                    score += weight
        scope = self.scope(job)
        if scope == "restricted":
            score -= 4
            reasons.append("-scope-outside-eu")
        if score >= self.high:
            decision = "high"
        elif score >= self.review:
            decision = "review"
        else:
            decision = "low"
        lane = self.lane(job)
        region = self.region(job)
        language = self.language(job)
        # "high" means in-lane and worth acting on today: a strong title outside every
        # target lane, or a remote posting that excludes Europe, stays visible as
        # "review" instead of jumping the queue.
        if decision == "high" and (lane == "other" or scope == "restricted"):
            decision = "review"
            reasons.append("-off-lane" if lane == "other" else "-out-of-scope")
        # Hard exclusions last: they outrank everything, and they are stored rather
        # than dropped so the reason stays inspectable (`list --decision out`).
        if language not in self.allowed_languages:
            decision = "out"
            reasons.append(f"-lang-{language}")
        if region not in self.allowed_regions:
            decision = "out"
            reasons.append(f"-region-{region}")
        return Match(
            score=score,
            decision=decision,
            lane=lane,
            reasons=reasons,
            scope=scope,
            region=region,
            language=language,
        )


def is_relevant(job: Job, scorer: Scorer, min_score: int = 3) -> bool:
    return scorer.match(job).score >= min_score
