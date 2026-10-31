"""The answer pipeline: cheap answers first, verified ones when it matters.

Order of operations for one question:

1. **Cache** - a near-identical earlier question with the same repository returns
   instantly (cosine similarity over hashing embeddings, stored locally).
2. **Quick answer** - the fast model answers first, streamed, so the UI shows text
   in about a second.
3. **Plan** (hard tasks only) - a short numbered plan from the reasoning model,
   never shown unless asked.
4. **Candidates** (hard tasks only) - 2-3 samples at different temperatures.
5. **Score, don't average** - each candidate is checked for invented file paths,
   unparseable code, security problems and missing citations. The best score wins;
   the judge model is only consulted on an exact tie. Answers are never concatenated.

Every step is advisory: with a single llama.cpp model the pipeline simply samples
that model instead of pretending a second one exists.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from collections.abc import Callable, Sequence
from typing import Any

from .confidence import Confidence, assess
from .security_scan import blocking, scan_text

_CITATION = re.compile(r"\[[CM]\d+\]")
_PATH = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:py|js|mjs|cjs|ts|tsx|jsx|java|kt|kts|c|h|cc|cpp|hpp|"
    r"cs|go|rs|rb|php|swift|dart|sql|sh|bash|ps1|html|css|scss|json|toml|yaml|yml|md))"
    r"(?::\d+(?:-\d+)?)?"
)
_FENCE = re.compile(r"```([A-Za-z0-9_+-]*)\n(.*?)```", re.S)
_VERIFY_WORDS = ("pytest", "npm test", "cargo test", "go test", "make test", "ruff", "tsc")


@dataclass
class Candidate:
    text: str
    model: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.score > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "score": round(self.score, 2),
            "reasons": self.reasons,
            "chars": len(self.text),
        }


@dataclass
class PipelineResult:
    answer: str
    model: str
    quick: str = ""
    plan: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    cached: bool = False
    escalated: bool = False
    judge_note: str = ""
    confidence: Confidence | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "model": self.model,
            "quick": self.quick,
            "plan": self.plan,
            "cached": self.cached,
            "escalated": self.escalated,
            "judge_note": self.judge_note,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "citations": self.citations,
            "confidence": self.confidence.to_dict() if self.confidence else None,
            "error": self.error or None,
        }


def _code_blocks(text: str) -> list[tuple[str, str]]:
    blocks = [(lang.lower(), body) for lang, body in _FENCE.findall(text)]
    return blocks or [("", text)]


def score_candidate(
    text: str,
    *,
    language: str = "",
    cited_paths: Sequence[str] = (),
    allow_unknown_paths: bool = False,
) -> tuple[float, list[str]]:
    """Score one answer on checkable properties, not on how confident it sounds."""
    reasons: list[str] = []
    score = 0.0
    if not text.strip():
        return -5.0, ["empty answer"]

    cited = set(cited_paths)
    citations = len(_CITATION.findall(text))
    if citations:
        score += min(3.0, 0.75 * citations)
        reasons.append(f"{citations} citation marker(s)")

    mentioned = {match for match in _PATH.findall(text) if not match.startswith(("http", "https"))}
    if cited:
        known = {path for path in mentioned if path in cited}
        unknown = {path for path in mentioned if path not in cited}
        if known:
            score += min(2.5, 1.0 * len(known))
            reasons.append(f"{len(known)} mentioned file(s) exist in context")
        if unknown:
            penalty = min(4.0, 1.5 * len(unknown))
            score -= penalty
            reasons.append(
                f"mentions {len(unknown)} file(s) not in context: " + ", ".join(sorted(unknown)[:3])
            )
    elif mentioned and not allow_unknown_paths:
        score -= min(2.0, 0.5 * len(mentioned))
        reasons.append("mentions project files but no code context was supplied")

    for lang, body in _code_blocks(text):
        block_language = lang or language
        findings = blocking(scan_text("<answer>", body, language=block_language))
        if findings:
            score -= 4.0
            reasons.append(
                "code triggers security rules: " + ", ".join(sorted({f.rule for f in findings}))
            )
        if block_language == "python" and body.strip():
            try:
                ast.parse(body)
                score += 1.5
                reasons.append("python block parses")
            except SyntaxError:
                score -= 3.0
                reasons.append("python block has a syntax error")
        elif body.strip():
            if body.count("{") == body.count("}") and body.count("(") == body.count(")"):
                score += 0.5
            else:
                score -= 1.0
                reasons.append("unbalanced brackets in code block")

    if text.count("```") % 2:
        score -= 1.5
        reasons.append("unclosed code fence")

    lowered = text.lower()
    if any(word in lowered for word in _VERIFY_WORDS):
        score += 0.5
        reasons.append("says how to verify")

    return score, reasons


async def collect(
    backend: Any, messages: list[dict[str, str]], model: str, options: dict[str, Any] | None
) -> str:
    """Join a streamed backend reply into one string."""
    pieces: list[str] = []
    async for chunk in backend.chat(messages, model, options=options):
        pieces.append(chunk)
    return "".join(pieces)


class AnswerPipeline:
    """Cache → quick → plan → candidates → judge, with a hard budget."""

    def __init__(
        self,
        backend: Any,
        *,
        store: Any = None,
        config: Any = None,
        repo: str = "",
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.backend = backend
        self.store = store
        self.config = config
        self.repo = repo
        self.on_event = on_event
        self.pipeline_config = getattr(config, "pipeline", None)

    # ------------------------------------------------------------------ events
    def _emit(self, kind: str, **payload: Any) -> None:
        if self.on_event is not None:
            self.on_event({"event": kind, **payload})

    # ------------------------------------------------------------------- cache
    def cache_lookup(self, prompt: str) -> dict[str, Any] | None:
        if self.store is None or self.pipeline_config is None or not self.pipeline_config.cache:
            return None
        return self.store.cache_lookup(
            prompt, repo=self.repo, threshold=self.pipeline_config.cache_threshold
        )

    def cache_store(
        self,
        prompt: str,
        result: PipelineResult,
        *,
        citations: Sequence[dict[str, Any]] = (),
    ) -> None:
        if self.store is None or self.pipeline_config is None or not self.pipeline_config.cache:
            return
        if not result.answer.strip() or result.error or not result.confidence:
            return
        if result.confidence.score < self.pipeline_config.min_confidence_to_cache:
            return
        self.store.cache_put(
            prompt,
            answer=result.answer,
            model=result.model,
            repo=self.repo,
            citations=list(citations),
            confidence=result.confidence.score,
        )

    # -------------------------------------------------------------------- run
    async def run(
        self,
        prompt: str,
        messages: list[dict[str, str]],
        *,
        difficulty: str,
        language: str = "",
        cited_paths: Sequence[str] = (),
        quick_model: str,
        strong_model: str | None = None,
        judge_model: str | None = None,
        options: dict[str, Any] | None = None,
        checks: Sequence[Any] = (),
        sources: int = 0,
        use_cache: bool = True,
        quick_text: str | None = None,
        demo: bool = False,
    ) -> PipelineResult:
        options = dict(options or {})
        hard = difficulty == "hard"

        if use_cache:
            cached = self.cache_lookup(prompt)
            if cached is not None:
                cached_score = float(cached.get("confidence") or 0.0)
                label = (
                    "high" if cached_score >= 0.75 else "medium" if cached_score >= 0.5 else "low"
                )
                confidence = Confidence(
                    cached_score,
                    label,
                    [
                        f"cached answer from {cached.get('model')} (similarity {cached['similarity']:.2f})"
                    ],
                )
                self._emit(
                    "cache",
                    similarity=cached["similarity"],
                    model=cached.get("model", ""),
                )
                return PipelineResult(
                    answer=cached["answer"],
                    model=str(cached.get("model", "")),
                    cached=True,
                    confidence=confidence,
                    citations=list(cached.get("citations") or []),
                )

        if quick_text is None:
            quick = await collect(self.backend, messages, quick_model, options)
            self._emit("quick", model=quick_model, text=quick)
        else:
            # The caller already streamed this answer to the user; do not pay twice.
            quick = quick_text
        result = PipelineResult(answer=quick, model=quick_model, quick=quick)

        plan = ""
        if hard and self.pipeline_config and self.pipeline_config.plan_hard_tasks:
            plan = await self._plan(messages, strong_model or quick_model, options)
            result.plan = plan
            if plan:
                self._emit("plan", text=plan)

        candidates = [Candidate(quick, quick_model)]
        if hard:
            extra_models = (
                [strong_model] if strong_model and strong_model != quick_model else [quick_model]
            )
            needed = max(
                1, (self.pipeline_config.max_candidates if self.pipeline_config else 3) - 1
            )
            for index in range(needed):
                model = extra_models[0] if extra_models else quick_model
                variant = self._messages_with(messages, plan=plan, variant=index)
                text = await collect(
                    self.backend, variant, model, self._sample_options(options, index)
                )
                candidates.append(Candidate(text, model))

            for candidate in candidates:
                candidate.score, candidate.reasons = score_candidate(
                    candidate.text,
                    language=language,
                    cited_paths=cited_paths,
                )
            candidates.sort(key=lambda item: item.score, reverse=True)
            best = candidates[0]
            tie = [c for c in candidates if abs(c.score - best.score) < 0.01]
            judge_note = ""
            winner = best
            if (
                len(tie) > 1
                and judge_model
                and self.pipeline_config
                and self.pipeline_config.judge_tie_break
            ):
                judge_note, chosen = await self._judge(prompt, tie, judge_model, options)
                self._emit("judge", note=judge_note)
                if chosen is not None:
                    winner = tie[chosen]
            result.answer = winner.text
            result.model = winner.model
            result.escalated = winner.model != quick_model or winner.text != quick
            result.candidates = candidates
            result.judge_note = judge_note

        result.confidence = assess(
            result.answer,
            sources=sources,
            check_results=checks,
            rounds=0,
            demo=demo,
        )
        if not result.cached and not demo:
            self.cache_store(prompt, result)
        return result

    # ---------------------------------------------------------------- helpers
    def _sample_options(self, options: dict[str, Any], index: int) -> dict[str, Any]:
        varied = dict(options)
        # Different temperatures give genuinely different samples; the seed keeps
        # runs reproducible for the same question.
        varied["temperature"] = min(1.0, 0.2 + 0.3 * index)
        varied["seed"] = int(options.get("seed", 0)) + index + 1
        return varied

    def _messages_with(
        self, messages: list[dict[str, str]], *, plan: str, variant: int
    ) -> list[dict[str, str]]:
        if not plan:
            return messages
        extra = (
            "Work from this short plan, and change it if the code contradicts it:\n"
            f"{plan.strip()}\n\n"
            "Now answer the original request. Follow the plan, cite files you were given, "
            "and say what you could not verify."
        )
        variant_note = f"\n\n(independent attempt {variant + 2}: do not repeat previous wording)"
        return [
            *messages[:-1],
            {"role": "user", "content": messages[-1]["content"] + "\n\n" + extra + variant_note},
        ]

    async def _plan(
        self, messages: list[dict[str, str]], model: str, options: dict[str, Any]
    ) -> str:
        plan_messages = [
            {
                "role": "system",
                "content": (
                    "You are a planning assistant. Produce a short numbered plan (max 5 steps) "
                    "for solving the user's task. No code, no prose, no restating the question."
                ),
            },
            {"role": "user", "content": messages[-1]["content"][:4000]},
        ]
        try:
            plan = await collect(self.backend, plan_messages, model, {**options, "max_tokens": 200})
        except Exception as exc:
            self._emit("plan_failed", error=str(exc)[:200])
            return ""
        return plan.strip()[:800]

    async def _judge(
        self,
        prompt: str,
        candidates: list[Candidate],
        judge_model: str,
        options: dict[str, Any],
    ) -> tuple[str, int | None]:
        labels = "ABCDEFGH"
        rendered = "\n\n".join(
            f"--- {labels[index]} ---\n{candidate.text[:4000]}"
            for index, candidate in enumerate(candidates)
        )
        judge_messages = [
            {
                "role": "system",
                "content": (
                    "Pick the answer that is most likely correct and least likely to invent APIs "
                    "or files. Reply with one letter only."
                ),
            },
            {
                "role": "user",
                "content": f"Question:\n{prompt}\n\nAnswers:\n{rendered}\n\nBest answer (A/B/C):",
            },
        ]
        try:
            reply = (await collect(self.backend, judge_messages, judge_model, options)).strip()
        except Exception as exc:
            return f"judge unavailable: {str(exc)[:120]}", None
        for index, label in enumerate(labels[: len(candidates)]):
            if label in reply.upper():
                candidates[index].score += 0.5
                return f"judge chose {label} ({candidates[index].model})", index
        return "judge reply was not a letter; keeping the highest-scoring candidate", None
