"""Pipeline behaviour: cache first, sample on hard tasks, never average answers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from devmind.config import Config
from devmind.memory.store import MemoryStore
from devmind.pipeline import AnswerPipeline, score_candidate


class ScriptedBackend:
    name = "scripted"

    def __init__(self, replies: list[str]):
        self.replies = replies
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.messages: list[list[dict[str, str]]] = []

    async def chat(self, messages, model: str, *, options=None, keep_alive=None):
        self.messages.append(messages)
        self.calls.append((model, dict(options or {})))
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        yield reply

    async def aclose(self) -> None:
        return None


def _config(tmp_path: Path) -> Config:
    return Config.load(tmp_path / "home")


def _run(pipeline: AnswerPipeline, prompt: str, **kwargs: Any):
    defaults = {
        "difficulty": "normal",
        "quick_model": "fast",
        "options": {"temperature": 0.1, "seed": 1},
    }
    defaults.update(kwargs)
    return asyncio.run(
        pipeline.run(
            prompt,
            [{"role": "system", "content": "sys"}, {"role": "user", "content": prompt}],
            **defaults,
        )
    )


# --------------------------------------------------------------------- scoring


def test_scoring_rewards_citations_and_real_paths() -> None:
    good = "Use `pkg/billing.py:12` [C1].\n```python\nx = 1\n```\nRun `pytest -q`."
    score, reasons = score_candidate(good, language="python", cited_paths=["pkg/billing.py"])
    assert score > 3
    assert any("citation" in reason for reason in reasons)


def test_scoring_punishes_invented_files() -> None:
    invented = "Change `pkg/imaginary.py` to call the new helper."
    score, reasons = score_candidate(invented, cited_paths=["pkg/billing.py"])
    assert score < 0
    assert any("not in context" in reason for reason in reasons)


def test_scoring_rejects_unsafe_and_broken_code() -> None:
    unsafe = "```python\nvalue = eval(payload)\n```"
    broken = "```python\ndef f(:\n    pass\n```"
    assert score_candidate(unsafe, language="python")[0] < 0
    assert score_candidate(broken, language="python")[0] < 0


# -------------------------------------------------------------------- pipeline


def test_normal_task_answers_once_and_caches(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    backend = ScriptedBackend(["Refund via `pkg/billing.py` [C1]. Run `pytest -q`."])
    pipeline = AnswerPipeline(backend, store=store, config=_config(tmp_path), repo="shop")
    result = _run(
        pipeline,
        "how do refunds work?",
        cited_paths=["pkg/billing.py"],
        sources=1,
    )
    assert not result.cached
    assert backend.calls[0][0] == "fast"
    assert result.confidence is not None and result.confidence.score >= 0.4
    assert store.cache_stats()["entries"] == 1
    store.close()


def test_second_identical_question_hits_the_cache(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    backend = ScriptedBackend(["answer with `pkg/billing.py` [C1]"])
    pipeline = AnswerPipeline(backend, store=store, config=_config(tmp_path), repo="shop")
    _run(pipeline, "how do refunds work?", cited_paths=["pkg/billing.py"], sources=1)
    calls = len(backend.calls)
    again = _run(pipeline, "how do refunds work?", cited_paths=["pkg/billing.py"], sources=1)
    assert again.cached
    assert len(backend.calls) == calls
    assert again.confidence is not None
    store.close()


def test_hard_task_plans_and_samples_three_candidates(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    backend = ScriptedBackend(
        [
            "Quick guess that edits `pkg/ghost.py`.",  # quick, streamed first
            "1. read the code\n2. change it\n3. run tests",  # plan
            "Use `pkg/billing.py:10` [C1].\n```python\nx = 1\n```\nRun `pytest -q`.",  # sample 1
            "Another guess touching `pkg/ghost2.py`.",  # sample 2
        ]
    )
    pipeline = AnswerPipeline(backend, store=store, config=_config(tmp_path), repo="shop")
    result = _run(
        pipeline,
        "why does the refund test deadlock?",
        difficulty="hard",
        cited_paths=["pkg/billing.py"],
        strong_model="strong",
        sources=2,
    )
    assert result.plan.startswith("1.")
    assert len(result.candidates) == 3
    assert "pkg/billing.py" in result.answer
    assert "ghost" not in result.answer
    assert result.escalated
    # quick, plan, two samples
    assert len(backend.calls) == 4
    temps = [call[1].get("temperature") for call in backend.calls[2:]]
    assert len(set(temps)) == len(temps)
    store.close()


def test_judge_breaks_an_exact_tie(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    backend = ScriptedBackend(
        [
            "alpha answer",  # quick
            "1. plan",  # plan
            "beta answer",  # sample 1
            "B",  # judge
        ]
    )
    pipeline = AnswerPipeline(backend, store=store, config=_config(tmp_path))
    result = _run(
        pipeline,
        "hard question",
        difficulty="hard",
        strong_model="strong",
        judge_model="judge",
        cited_paths=(),
    )
    assert "judge chose" in result.judge_note
    assert result.answer == "beta answer"
    store.close()


def test_cache_can_be_disabled(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.cache_put("what is a monad?", answer="cached", model="m1")
    config = _config(tmp_path)
    config.pipeline.cache = False
    backend = ScriptedBackend(["fresh answer"])
    pipeline = AnswerPipeline(backend, store=store, config=config)
    result = _run(pipeline, "what is a monad?")
    assert not result.cached
    assert result.answer == "fresh answer"
    store.close()
