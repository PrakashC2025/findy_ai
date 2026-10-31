"""Turn the memory store into a small, cited prompt block.

Retrieval is deliberately conservative: a few high-precision memories beat a
wall of context.  Every injected line carries an id (``M1``, ``M2`` …) so the
answer can cite it and the UI can show what the model was told.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..chunkers import language_for
from ..code import CodeHit
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
    code_hits: list[CodeHit] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block": self.block,
            "hits": [hit.to_dict() for hit in self.hits],
            "code_hits": [hit.to_dict() for hit in self.code_hits],
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
    def business_block(self, limit: int = 3) -> str:
        """The user's business/project brief, always included when present."""
        rows = self.store.list_memories(kind="business", limit=limit)
        if not rows:
            return ""
        lines = [
            "BUSINESS CONTEXT (written by the user; use it to pick trade-offs, "
            "tone and priorities):"
        ]
        lines += [f"- {str(row.get('text', '')).strip()}" for row in rows if row.get("text")]
        return "\n".join(lines)

    def context_for(
        self,
        prompt: str,
        *,
        extra_kinds: tuple[str, ...] | None = None,
        repo: str | None = None,
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

        citations: list[dict[str, Any]] = []
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

        block = ""
        if lines:
            block = (
                "What you already know about this user and project "
                "(cite as [M1], [M2] ... when you rely on it):\n" + "\n".join(lines)
            )
        preferences = [hit.text for hit in ordered if hit.kind in {"preference", "rule"}][:6]

        business = self.business_block()
        if business:
            block = f"{business}\n\n{block}" if block else business

        code_block, code_hits, code_citations = self._code_context(prompt, repo)
        if code_block:
            block = f"{block}\n\n{code_block}" if block else code_block

        return MemoryContext(
            block=block,
            hits=ordered,
            code_hits=code_hits,
            citations=[*citations, *code_citations],
            preferences=preferences,
        )

    def _code_context(
        self, prompt: str, repo: str | None
    ) -> tuple[str, list[CodeHit], list[dict[str, Any]]]:
        """Relevant, cited code from the indexed project (empty if not indexed)."""
        if not (repo and self.config.include_code and self.config.code_top_k > 0):
            return "", [], []

        hits = self.store.search_code(prompt, repo=repo, top_k=self.config.code_top_k)
        if not hits:
            return "", [], []

        budget = self.config.code_max_chars
        used = 0
        lines = [
            "Relevant code from the user's project "
            "(cite as [C1], [C2] ... with file:line; never invent paths):"
        ]
        citations: list[dict[str, Any]] = []
        kept: list[CodeHit] = []
        for index, hit in enumerate(hits, start=1):
            label = f"C{index}"
            language = language_for(hit.path)
            header = f"[{label}] {hit.location}"
            if hit.symbol:
                header += f" ({hit.kind} {hit.symbol})"
            remaining = budget - used - len(header) - 20
            if remaining < 120:
                break
            snippet = hit.text
            if len(snippet) > remaining:
                snippet = snippet[:remaining] + "\n... (truncated)"
            block = f"{header}\n```{language}\n{snippet}\n```"
            lines.append(block)
            used += len(block)
            kept.append(hit)
            citations.append(
                {
                    "id": label,
                    "kind": "code",
                    "repo": hit.repo,
                    "path": hit.path,
                    "start_line": hit.start_line,
                    "end_line": hit.end_line,
                    "symbol": hit.symbol,
                }
            )
        return "\n".join(lines), kept, citations

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
