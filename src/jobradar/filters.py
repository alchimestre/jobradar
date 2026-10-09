"""Scoring, lane detection and remote-scope triage. Pure functions, no I/O."""

from __future__ import annotations

import re

from .config import (
    BROAD_GEO_RE,
    ENGLISH_MARKERS,
    FRENCH_MARKERS,
    GERMAN_MARKERS,
    HYBRID_RE,
    LANE_RULES,
    NON_TARGET_COUNTRY_RE,
    REGION_IN_SCOPE,
    REGION_OUT_OF_SCOPE,
    REMOTE_RE,
    SCOPE_BLOCKED_RE,
    SCOPE_OPEN_RE,
    TARGET_COUNTRY_RULES,
    WORLDWIDE_RE,
)
from .models import Job, Match

REMOTE = re.compile(REMOTE_RE, re.I)
SCOPE_BLOCKED = re.compile(SCOPE_BLOCKED_RE, re.I)
SCOPE_OPEN = re.compile(SCOPE_OPEN_RE, re.I)
HYBRID = re.compile(HYBRID_RE, re.I)
BROAD_GEO = re.compile(BROAD_GEO_RE, re.I)
NON_TARGET_COUNTRY = re.compile(NON_TARGET_COUNTRY_RE, re.I)

REGION_IN = [(region, re.compile(p, re.I)) for region, p in REGION_IN_SCOPE]
REGION_OUT = [(region, re.compile(p, re.I)) for region, p in REGION_OUT_OF_SCOPE]
TARGET_COUNTRY = [(code, re.compile(p, re.I)) for code, p in TARGET_COUNTRY_RULES]
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
        target_countries: tuple[str, ...] = ("fr", "de", "ch", "nl"),
    ):
        self.title = _rules(title_rules)
        self.negative = _rules(negative_rules)
        self.high, self.review = high, review
        self.lanes = [(lane, re.compile(p, re.I)) for lane, p in LANE_RULES]
        self.allowed_languages = tuple(allowed_languages)
        self.allowed_regions = tuple(allowed_regions)
        self.target_countries = tuple(target_countries)
        self.country_rules = [(code, pattern) for code, pattern in TARGET_COUNTRY if code in self.target_countries]

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

    def _target_in(self, text: str) -> str:
        for code, pattern in self.country_rules:
            if pattern.search(text):
                return code
        return "unknown"

    def country(self, job: Job) -> str:
        """fr | de | ch | nl | other | unknown.

        Target countries win over everything ("Madrid / Berlin" is a Berlin posting too).
        When the location says nothing — an empty ATS field, "Headquarters", "Remote" —
        the JD body is consulted as a last resort, but only for a *target* country:
        a description that merely mentions some other country is far too noisy to gate on.

        The non-target list is read from the location alone: a job title is allowed to say
        "join us", and the bare "US" that region patterns miss only ever appears there.
        """
        hay = f"{job.location} {job.title}"
        code = self._target_in(hay)
        if code != "unknown":
            return code
        if NON_TARGET_COUNTRY.search(job.location):
            return "other"
        if job.description:
            return self._target_in(job.description[:1200])
        return "unknown"

    def mode(self, job: Job) -> str:
        """remote | hybrid | onsite | unknown — how the role is worked, from the location.

        An explicit "hybrid" in the *location* beats the source's own remote flag: Ashby
        marks postings `isRemote: true` while their location reads "The Hague - hybrid",
        which stored hybrid roles as remote and let them past the physical-role checks.
        The title is deliberately not consulted — "Hybrid Cloud Engineer" is a topic, not
        a working arrangement.
        """
        hay = f"{job.location} {job.title}"
        if HYBRID.search(job.location):
            return "hybrid"
        if job.remote or REMOTE.search(hay):
            return "remote"
        return "onsite" if job.location.strip() else "unknown"

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

    def lane(self, job: Job, country: str | None = None) -> str:
        hay = f"{job.location} {job.title}"
        if REMOTE.search(hay) and not any(p.search(job.location) for _, p in self.lanes):
            return "eu-remote"
        for lane, pattern in self.lanes:
            if pattern.search(hay):
                return lane
        # The gate has already established the country; a target country outside the lane
        # city lists ("Béthune", "Essen") is still that lane, not "other".
        place = country if country is not None else self.country(job)
        return place if place in self.target_countries else "other"

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
        country = self.country(job)
        lane = self.lane(job, country)
        region = self.region(job)
        language = self.language(job)
        mode = self.mode(job)
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
        # Geography gate. Physical work has to be in a target country; remote work has to
        # be workable from one, either by naming it or by naming a broad region that
        # contains it. A remote posting that only names somewhere else, or a physical one
        # whose location cannot be placed, is noise rather than an opportunity. The reason
        # is recorded even when another rule already excluded the row: it is the audit trail.
        if mode == "remote":
            broad = bool(BROAD_GEO.search(hay))
            if not (broad or country in self.target_countries or country == "unknown"):
                decision = "out"
                reasons.append("-remote-not-target")
            elif decision != "out" and not broad and country == "unknown":
                # Nothing in the posting places it. Kept — a bare "Remote" is often a real
                # EU-remote job — but tagged so the unverifiable rows can be audited.
                reasons.append("-geo-unverified")
        elif country not in self.target_countries:
            decision = "out"
            reasons.append(f"-{mode}-not-target")
        return Match(
            score=score,
            decision=decision,
            lane=lane,
            reasons=reasons,
            scope=scope,
            region=region,
            language=language,
            mode=mode,
            country=country,
        )


def is_relevant(job: Job, scorer: Scorer, min_score: int = 3) -> bool:
    return scorer.match(job).score >= min_score
