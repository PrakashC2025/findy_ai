"""Turn the memory store into a small, cited prompt block.

Retrieval is deliberately conservative: a few high-precision memories beat a
wall of context.  Every injected line carries an id (``M1``, ``M2`` …) so the
answer can cite it and the UI can show what the model was told.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..config import MemoryConfig
from .store import Hit, MemoryStore

#: Clause openers that signal a question or a conditional, never a preference.
_NON_STATEMENT_STARTS = {
    "instead",
    "if",
    "when",
    "whether",
    "to",
    "a",
    "an",
    "the",
    "this",
    "that",
    "it",
    "they",
    "we",
    "i",
    "you",
    "there",
    "here",
    "not",
    "also",
    "ever",
}

#: Sentences that look like durable user preferences ("we use pnpm", "never use var").
_PREFERENCE_PATTERNS = (
    re.compile(
        r"\bwe (?:use|always use|standardise on|standardize on|run)\s+([^.,;\n]{2,60})", re.I
    ),
    re.compile(r"\b(?:always|never|please always|please never)\s+([^.,;\n]{3,70})", re.I),
    re.compile(
        r"\b(?:prefer|i prefer|our stack is|our team uses|in this repo we)\s+([^.,;\n]{3,80})", re.I
    ),
    re.compile(r"\bdon'?t use\s+([^.,;\n]{2,60})", re.I),
)


@dataclass
class MemoryContext:
    block: str = ""
    hits: list[Hit] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block": self.block,
            "hits": [hit.to_dict() for hit in self.hits],
            "citations": self.citations,
            "preferences": self.preferences,
        }

    def __bool__(self) -> bool:
        return bool(self.block.strip())


class Retriever:
    def __init__(self, store: MemoryStore, config: MemoryConfig):
        self.store = store
        self.config = config

    # ------------------------------------------------------------------ public
    def context_for(
        self, prompt: str, *, extra_kinds: tuple[str, ...] | None = None
    ) -> MemoryContext:
        if not self.config.enabled:
            return MemoryContext()
        hits = [
            hit
            for hit in self.store.search(prompt, top_k=self.config.top_k)
            if hit.score >= self.config.min_score
        ]
        if extra_kinds:
            hits.extend(
                hit
                for hit in self.store.search(prompt, top_k=2, kinds=extra_kinds)
                if hit.score >= self.config.min_score
            )
        # De-duplicate while keeping the best score per id.
        best: dict[str, Hit] = {}
        for hit in hits:
            if hit.id not in best or hit.score > best[hit.id].score:
                best[hit.id] = hit
        ordered = sorted(best.values(), key=lambda hit: hit.score, reverse=True)
        if not ordered:
            return MemoryContext()

        citations: list[dict[str, str]] = []
        lines: list[str] = []
        used = 0
        for index, hit in enumerate(ordered, start=1):
            label = f"M{index}"
            line = f"[{label}] ({hit.kind}) {hit.text.strip()}"
            if len(line) + used > self.config.max_chars_in_prompt:
                break
            lines.append(line)
            used += len(line)
            citations.append({"id": label, "kind": hit.kind, "text": hit.text})

        block = (
            "What you already know about this user and project "
            "(cite as [M1], [M2] … when you rely on it):\n" + "\n".join(lines)
        )
        preferences = [hit.text for hit in ordered if hit.kind in {"preference", "rule"}][:6]
        return MemoryContext(
            block=block, hits=ordered, citations=citations, preferences=preferences
        )

    # --------------------------------------------------------------- capturing
    def capture_preferences(self, text: str, *, session: str = "") -> list[str]:
        """Store explicit, durable statements from the user.

        Conservative on purpose: only declarative clauses are considered.  A
        question ("what do we use instead of npm?") must never become a memory.
        """
        if not (self.config.enabled and self.config.auto_capture_preferences):
            return []
        captured: list[str] = []
        for clause in re.split(r"(?<=[.!?;])\s+|\n+", text):
            clause = clause.strip()
            if not clause or "?" in clause:
                continue
            for pattern in _PREFERENCE_PATTERNS:
                match = pattern.search(clause)
                if not match:
                    continue
                statement = match.group(0).strip(" .;,\n")
                tail = match.group(1).strip().lower() if match.groups() else ""
                if not statement or len(statement) < 8 or len(statement) > 140:
                    continue
                if tail.split()[0] in _NON_STATEMENT_STARTS if tail else False:
                    continue
                self.store.remember(
                    statement,
                    kind="preference",
                    source="auto-capture",
                    meta={"session": session},
                    dedupe_threshold=0.90,
                )
                captured.append(statement)
                break
        return captured
