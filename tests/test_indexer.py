from __future__ import annotations

from pathlib import Path

from devmind.config import IndexConfig
from devmind.indexer import CodeIndexer
from devmind.memory.store import MemoryStore


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_project(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    write(
        root / "services" / "billing.py",
        "def refund_order(order_id):\n"
        '    """Refund an order exactly once."""\n'
        "    return order_id\n",
    )
    write(root / "web" / "cart.js", "function addToCart(item) {\n  return item;\n}\n")
    write(root / "README.md", "# Shop\n")
    write(root / "node_modules" / "left-pad" / "index.js", "module.exports = 1;\n")
    write(root / ".env", "STRIPE_SECRET=abc123\n")
    return root


def test_index_reports_and_stores(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    store = MemoryStore(tmp_path / "m.db")
    report = CodeIndexer(store, IndexConfig()).index(root, repo="shop")

    assert report.complete is True
    assert report.indexed == 2  # billing.py + cart.js
    assert report.chunks >= 2
    assert report.symbols >= 2
    assert report.errors == []

    stats = store.code_stats("shop")
    assert stats["files"] == 2
    assert stats["chunks"] == report.chunks

    hits = store.search_code("refund an order", repo="shop", top_k=3)
    assert hits
    assert hits[0].path == "services/billing.py"
    assert hits[0].start_line == 1


def test_excluded_and_secret_files_are_not_read(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    store = MemoryStore(tmp_path / "m.db")
    CodeIndexer(store, IndexConfig()).index(root, repo="shop")
    assert store.code_stats("shop")["files"] == 2
    assert not store.search_code("left pad module", repo="shop", top_k=10)
    assert not store.search_code("stripe secret", repo="shop", top_k=10)


def test_second_run_is_incremental(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    store = MemoryStore(tmp_path / "m.db")
    indexer = CodeIndexer(store, IndexConfig())
    first = indexer.index(root, repo="shop")
    second = indexer.index(root, repo="shop")

    assert second.indexed == 0
    assert second.skipped == first.indexed
    assert second.chunks == 0


def test_changed_file_is_reindexed_and_old_chunks_removed(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    store = MemoryStore(tmp_path / "m.db")
    indexer = CodeIndexer(store, IndexConfig())
    indexer.index(root, repo="shop")
    assert store.search_code("refund_order", repo="shop")

    write(root / "services" / "billing.py", "def issue_credit(order_id):\n    return order_id\n")
    report = indexer.index(root, repo="shop")
    assert report.indexed == 1

    old = store.search_code("refund_order", repo="shop", top_k=5)
    new = store.search_code("issue_credit", repo="shop", top_k=5)
    assert all("refund_order" not in hit.text for hit in old)
    assert any(hit.path == "services/billing.py" for hit in new)


def test_deleted_file_is_pruned(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    store = MemoryStore(tmp_path / "m.db")
    indexer = CodeIndexer(store, IndexConfig())
    indexer.index(root, repo="shop")

    (root / "web" / "cart.js").unlink()
    report = indexer.index(root, repo="shop")
    assert report.removed == 1
    assert store.code_stats("shop")["files"] == 1


def test_truncated_run_is_resumable_and_does_not_prune(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    store = MemoryStore(tmp_path / "m.db")
    indexer = CodeIndexer(store, IndexConfig(max_seconds=0))

    report = indexer.index(root, repo="shop")
    assert report.complete is False
    assert report.reason.startswith("max_seconds") or report.reason.startswith("max_files")
    assert not report.removed

    full = indexer.index(root, repo="shop", max_seconds=60)
    assert full.complete is True
    assert full.indexed >= 2


def test_large_files_are_skipped(tmp_path: Path) -> None:
    root = tmp_path / "big"
    write(root / "huge.py", "x = 1\n" * 5000)
    write(root / "ok.py", "def tiny():\n    return 1\n")
    store = MemoryStore(tmp_path / "m.db")
    report = CodeIndexer(store, IndexConfig(max_file_bytes=1000)).index(root, repo="big")
    assert report.indexed == 1
    assert report.skipped == 1


def test_repo_state_is_stored(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    store = MemoryStore(tmp_path / "m.db")
    CodeIndexer(store, IndexConfig()).index(root, repo="shop")
    state = store.get_index_state("shop")
    assert state is not None
    assert state["complete"] is True
    assert state["root"] == str(root.resolve())
