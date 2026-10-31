from __future__ import annotations

from pathlib import Path

from devmind.config import MemoryConfig
from devmind.memory.retriever import Retriever
from devmind.memory.store import MemoryStore


def make_store(path: Path) -> MemoryStore:
    return MemoryStore(path)


def test_remember_and_search(tmp_path: Path) -> None:
    store = make_store(tmp_path / "m.db")
    store.remember("We use pnpm, never npm, in this monorepo.", kind="preference")
    store.remember(
        "The billing service lives in services/billing and uses Postgres 16.", kind="fact"
    )
    assert store.memory_count() == 2

    hits = store.search("which package manager do we use?", top_k=3)
    assert hits
    assert "pnpm" in hits[0].text

    unrelated = store.search("how do I centre a div with flexbox?", top_k=3)
    assert all("pnpm" not in hit.text for hit in unrelated)


def test_near_duplicates_fold_instead_of_piling_up(tmp_path: Path) -> None:
    store = make_store(tmp_path / "m.db")
    first = store.remember("Always use type hints in Python")
    second = store.remember("Always use type hints in Python.")
    assert first == second
    assert store.memory_count() == 1
    assert store.get_memory(first)["weight"] > 1.0


def test_forget_and_wipe(tmp_path: Path) -> None:
    store = make_store(tmp_path / "m.db")
    memory_id = store.remember("Use ruff for linting")
    assert store.forget(memory_id) is True
    assert store.forget(memory_id) is False
    store.remember("Use pytest")
    store.record_episode("s1", "user", "hello")
    store.wipe()
    assert store.memory_count() == 0
    assert store.stats()["episodes"] == 0


def test_episodes_and_feedback(tmp_path: Path) -> None:
    store = make_store(tmp_path / "m.db")
    store.record_episode("s1", "user", "write a test")
    store.record_episode("s1", "assistant", "here is a test")
    store.record_episode("s2", "user", "other session")

    session = store.recent_episodes("s1", limit=10)
    assert [episode["role"] for episode in session] == ["user", "assistant"]

    store.add_feedback(1, answer_id="a1", task="tests", model="m", correction="use parametrize")
    summary = store.feedback_summary()
    assert summary["positive"] == 1
    rules = store.list_memories(kind="rule")
    assert rules and "parametrize" in rules[0]["text"]


def test_model_stats_learn(tmp_path: Path) -> None:
    store = make_store(tmp_path / "m.db")
    store.record_outcome("bug_fix", "qwen2.5-coder:7b", True, 0.9, 1200)
    store.record_outcome("bug_fix", "qwen2.5-coder:7b", True, 0.8, 1000)
    stats = store.model_stats("bug_fix")
    assert stats["qwen2.5-coder:7b"]["runs"] == 2
    assert stats["qwen2.5-coder:7b"]["wins"] == 2
    assert stats["qwen2.5-coder:7b"]["alpha"] > 1.0


def test_routing_log(tmp_path: Path) -> None:
    store = make_store(tmp_path / "m.db")
    route_id = store.log_route(
        session="s1",
        task="write_new",
        language="python",
        difficulty=0.4,
        model="qwen2.5-coder:7b",
        reason="test",
        prompt_chars=42,
    )
    store.set_route_outcome(route_id, "answered")
    routes = store.recent_routes()
    assert routes[0]["model"] == "qwen2.5-coder:7b"
    assert routes[0]["outcome"] == "answered"


def test_export_import_roundtrip(tmp_path: Path) -> None:
    first = make_store(tmp_path / "a.db")
    first.remember("We deploy with Fly.io", kind="fact")
    first.upsert_skill("add-endpoint", "Add a FastAPI endpoint", ["edit router", "add test"])

    second = make_store(tmp_path / "b.db")
    added = second.import_json(first.export_json())
    assert added == 1
    assert second.memory_count() == 1
    assert second.list_skills()[0]["name"] == "add-endpoint"


def test_retriever_injects_only_relevant_memory(tmp_path: Path) -> None:
    store = make_store(tmp_path / "m.db")
    store.remember("We use pnpm, never npm", kind="preference")
    retriever = Retriever(store, MemoryConfig())

    relevant = retriever.context_for("how should I add a new npm dependency?")
    assert relevant.citations
    assert "pnpm" in relevant.block
    assert relevant.citations[0]["id"] == "M1"

    irrelevant = retriever.context_for("write a quicksort implementation in Rust")
    assert "pnpm" not in irrelevant.block
    assert irrelevant.citations == []


def test_preference_capture_is_conservative(tmp_path: Path) -> None:
    store = make_store(tmp_path / "m.db")
    retriever = Retriever(store, MemoryConfig())

    assert retriever.capture_preferences("We always use type hints in Python.") == [
        "We always use type hints in Python"
    ]
    # Questions and conditionals must not become memories.
    assert retriever.capture_preferences("What do we use instead of npm?") == []
    assert retriever.capture_preferences("If we use pnpm, will CI break?") == []


def test_retriever_respects_kill_switch(tmp_path: Path) -> None:
    store = make_store(tmp_path / "m.db")
    store.remember("We use pnpm", kind="preference")
    retriever = Retriever(store, MemoryConfig(enabled=False))
    assert not retriever.context_for("npm or pnpm?")
