"""Scoring, lane detection and remote-scope triage. Pure functions, no I/O."""

from __future__ import annotations

import re

from .config import LANE_RULES, REMOTE_RE, SCOPE_BLOCKED_RE, SCOPE_OPEN_RE
from .models import Job, Match

REMOTE = re.compile(REMOTE_RE, re.I)
SCOPE_BLOCKED = re.compile(SCOPE_BLOCKED_RE, re.I)
SCOPE_OPEN = re.compile(SCOPE_OPEN_RE, re.I)


def _rules(entries) -> list[tuple[re.Pattern, int, str]]:
    return [(re.compile(p, re.I), int(w), t) for p, w, t in entries]


HARD_NEGATIVES = {"intern", "leadership", "non-ic", "go-to-market", "off-target", "seniority-stretch"}


class Scorer:
    def __init__(self, title_rules, negative_rules, high: int = 6, review: int = 3):
        self.title = _rules(title_rules)
        self.negative = _rules(negative_rules)
        self.high, self.review = high, review
        self.lanes = [(lane, re.compile(p, re.I)) for lane, p in LANE_RULES]

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
        # "high" means in-lane and worth acting on today; a strong title outside every
        # target lane stays visible as "review" instead of jumping the queue.
        if decision == "high" and lane == "other":
            decision = "review"
            reasons.append("-off-lane")
        return Match(score=score, decision=decision, lane=lane, reasons=reasons, scope=scope)


def is_relevant(job: Job, scorer: Scorer, min_score: int = 3) -> bool:
    return scorer.match(job).score >= min_score
