"""Cheap, explainable difficulty classification.

Runs before any model call so trivial tasks never pay for reasoning and hard
tasks get a time budget.  Rules only: fast, deterministic, debuggable.  The
router can later replace this with a learned classifier without changing the
call site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

TRIVIAL = "trivial"
NORMAL = "normal"
HARD = "hard"

_TRACEBACK = re.compile(
    r"(Traceback \(most recent call last\)|^\s+at .+\(.+:\d+\)|\b\w+Error:)", re.M
)
_HARD_WORDS = (
    "architecture",
    "distributed",
    "concurrency",
    "race condition",
    "deadlock",
    "migration",
    "migrate",
    "refactor",
    "optimize",
    "performance",
    "complexity",
    "security audit",
    "multi-tenant",
    "scalab",
    "design a",
    "why does",
    "root cause",
    "async",
    "transaction",
    "index strategy",
    "accessib",
)
_TRIVIAL_STARTS = (
    "what is ",
    "what's ",
    "who is ",
    "define ",
    "rename ",
    "difference between ",
    "syntax for ",
    "how do i print",
    "capital of",
)
_PATH = re.compile(r"(?:[\w.-]+/)+[\w.-]+\.\w{1,6}")
_IMPERATIVE = re.compile(
    r"^(?:add|create|write|implement|fix|refactor|make|build|update|remove|change|"
    r"convert|migrate|debug|test|explain why|review|optimi[sz]e|design)\b",
    re.I,
)


@dataclass
class Difficulty:
    level: str = NORMAL
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def needs_reasoning(self) -> bool:
        return self.level == HARD

    def budget_s(self) -> float:
        return {TRIVIAL: 15.0, NORMAL: 90.0, HARD: 240.0}[self.level]

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "score": self.score,
            "reasons": self.reasons,
            "budget_s": self.budget_s(),
        }


def classify_difficulty(prompt: str, *, error_output: str = "") -> Difficulty:
    """Score a request 0-100 from length, code, errors and vocabulary."""
    text = prompt.strip()
    lowered = text.lower()
    score = 0
    reasons: list[str] = []

    if len(text) > 800:
        score += 25
        reasons.append("long, detailed request")
    elif len(text) > 300:
        score += 12
        reasons.append("detailed request")

    if "```" in text:
        score += 18
        reasons.append("contains code")

    if _TRACEBACK.search(text) or _TRACEBACK.search(error_output):
        score += 30
        reasons.append("contains an error/traceback")

    hits = [word for word in _HARD_WORDS if word in lowered]
    if hits:
        score += min(25, 8 * len(hits))
        reasons.append("hard vocabulary: " + ", ".join(sorted(hits)[:4]))

    paths = set(_PATH.findall(text))
    if len(paths) >= 3:
        score += 15
        reasons.append(f"touches {len(paths)} files")
    elif len(paths) == 2:
        score += 6
        reasons.append("touches 2 files")

    if lowered.startswith(_TRIVIAL_STARTS) and len(text) < 200 and "```" not in text:
        score -= 25
        reasons.append("short factual question")

    score = max(0, min(100, score))
    asks_something = "?" in text or lowered.startswith(_TRIVIAL_STARTS)
    if score >= 55:
        level = HARD
    elif score < 20 and len(text) < 120 and asks_something and not _IMPERATIVE.match(text):
        level = TRIVIAL
    else:
        level = NORMAL
    if not reasons:
        reasons.append("short request with no ambiguity signals")
    return Difficulty(level=level, score=score, reasons=reasons)
