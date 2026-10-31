"""Turn verified facts into a confidence score the user can act on.

The score is not a model's self-report - those are badly calibrated.  It is a
small, auditable function of what DevMind actually observed: how many cited
sources the answer used, whether the project's checks passed, whether a repair
loop converged, and whether the answer contains links that were never in the
context (a classic hallucination signal).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

_URL = re.compile(r"https?://[^\s)\]>\"']+")
_UNCERTAIN = (
    "not sure",
    "i don't know",
    "i do not know",
    "unverified",
    "i cannot verify",
    "probably",
    "might be",
    "guess",
)
_HEDGE = ("i think", "likely", "may ", "possibly")


@dataclass
class Confidence:
    score: float
    label: str  # high | medium | low | none
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"score": round(self.score, 2), "label": self.label, "reasons": self.reasons}

    def render(self) -> str:
        return f"confidence: {self.label} ({self.score:.2f}) — " + "; ".join(
            self.reasons or ["no evidence"]
        )


def _checks_summary(checks: Sequence[Any]) -> tuple[int, int]:
    passed = failed = 0
    for check in checks:
        if getattr(check, "skipped", False):
            continue
        if getattr(check, "ok", False):
            passed += 1
        else:
            failed += 1
    return passed, failed


def assess(
    answer: str,
    *,
    sources: int = 0,
    check_results: Sequence[Any] = (),
    rounds: int = 0,
    demo: bool = False,
    source_urls: Sequence[str] = (),
) -> Confidence:
    """Score an answer from observable evidence, never from model bravado."""
    text = answer.strip()
    reasons: list[str] = []
    if demo:
        return Confidence(
            0.05,
            "none",
            ["demo mode: no model produced this text, so there is nothing to trust"],
        )

    score = 0.40
    if not text:
        return Confidence(0.02, "none", ["empty answer"])

    if sources:
        bonus = min(0.20, 0.06 * sources)
        score += bonus
        reasons.append(f"{sources} cited source(s)")

    passed, failed = _checks_summary(check_results)
    if passed and not failed:
        score += 0.22
        reasons.append(f"{passed} check(s) passed")
    elif failed:
        score -= min(0.30, 0.15 * failed)
        reasons.append(f"{failed} check(s) failed")
    elif not passed and not failed:
        score -= 0.05
        reasons.append("no automated checks available")

    if rounds:
        score += 0.05
        reasons.append(f"self-repair converged in {rounds} round(s)")

    lowered = text.lower()
    if any(phrase in lowered for phrase in _UNCERTAIN):
        score -= 0.10
        reasons.append("the answer states uncertainty (treated as honest, not confident)")
    elif any(phrase in lowered for phrase in _HEDGE):
        score -= 0.05
        reasons.append("the answer hedges")

    links = set(_URL.findall(text))
    allowed = {url.rstrip(".,;") for url in source_urls}
    stray = {link for link in links if link.rstrip(".,;") not in allowed}
    if stray and allowed:
        score -= 0.15
        reasons.append(f"{len(stray)} link(s) not present in the retrieved sources")
    elif links and not allowed:
        score -= 0.10
        reasons.append("links were given but no sources were retrieved to check them")

    score = max(0.02, min(0.97, score))
    label = "high" if score >= 0.75 else "medium" if score >= 0.5 else "low"
    return Confidence(score, label, reasons)
