"""Semantic answer cache: same question twice is instant, not a new model call."""

from __future__ import annotations

from pathlib import Path

from devmind.memory.store import MemoryStore


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.db")


def test_identical_prompt_hits_and_counts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.cache_put(
        "how do I refund an order?", answer="call refund_order", model="m1", confidence=0.7
    )
    hit = store.cache_lookup("how do I refund an order?")
    assert hit is not None
    assert hit["answer"] == "call refund_order"
    assert hit["similarity"] >= 0.99
    assert hit["confidence"] == 0.7
    assert store.cache_stats() == {"entries": 1, "hits": 1}
    store.close()


def test_unrelated_prompt_misses(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.cache_put("refund an order", answer="x", model="m1")
    assert store.cache_lookup("write a rust parser for protobuf") is None
    assert store.cache_stats()["hits"] == 0
    store.close()


def test_cache_is_scoped_per_repository(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.cache_put("how does refund work?", answer="shop answer", model="m1", repo="shop")
    assert store.cache_lookup("how does refund work?", repo="other") is None
    assert store.cache_lookup("how does refund work?", repo="shop")["answer"] == "shop answer"
    store.close()


def test_threshold_is_respected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.cache_put("refund an order", answer="x", model="m1")
    assert store.cache_lookup("refund an order", threshold=0.999) is not None
    assert store.cache_lookup("add retries to the http client", threshold=0.99) is None
    store.close()


def test_cache_is_pruned_and_clearable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for index in range(5):
        store.cache_put(f"question {index}", answer="a", model="m1", max_rows=2)
    assert store.cache_stats()["entries"] == 2
    assert store.cache_clear() == 2
    assert store.cache_stats() == {"entries": 0, "hits": 0}
    store.close()
