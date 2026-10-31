from __future__ import annotations

from pathlib import Path

from devmind.config import MemoryConfig
from devmind.indexer import CodeIndexer
from devmind.memory.retriever import Retriever
from devmind.memory.store import MemoryStore
from devmind.prompts import build_messages
from devmind.router import classify


SOURCE = (
    "def refund_order(order_id: int) -> int:\n"
    '    """Refund a paid order exactly once."""\n'
    "    return order_id\n"
    "\n"
    "\n"
    "def capture_payment(order_id: int) -> int:\n"
    "    return order_id\n"
)


def build(tmp_path: Path) -> tuple[MemoryStore, str]:
    root = tmp_path / "shop"
    (root / "billing").mkdir(parents=True)
    (root / "billing" / "refund.py").write_text(SOURCE)
    store = MemoryStore(tmp_path / "m.db")
    CodeIndexer(store, __import__("devmind.config", fromlist=["IndexConfig"]).IndexConfig()).index(
        root, repo="shop"
    )
    return store, "shop"


def test_code_search_returns_file_and_line(tmp_path: Path) -> None:
    store, repo = build(tmp_path)
    hits = store.search_code("refund a paid order", repo=repo, top_k=3)
    assert hits
    top = hits[0]
    assert top.path == "billing/refund.py"
    assert top.start_line == 1
    assert "refund_order" in top.text
    assert top.location.startswith("billing/refund.py:")


def test_retriever_injects_cited_code(tmp_path: Path) -> None:
    store, repo = build(tmp_path)
    retriever = Retriever(store, MemoryConfig())
    context = retriever.context_for("how does refund_order work?", repo=repo)

    assert context.code_hits
    assert "[C1]" in context.block
    assert "billing/refund.py" in context.block
    citations = [citation for citation in context.citations if citation["kind"] == "code"]
    assert citations and citations[0]["path"] == "billing/refund.py"


def test_prompt_teaches_citation_format(tmp_path: Path) -> None:
    store, repo = build(tmp_path)
    retriever = Retriever(store, MemoryConfig())
    context = retriever.context_for("explain refund_order", repo=repo)
    messages = build_messages(
        "explain refund_order",
        profile=classify("explain refund_order"),
        context=context,
        config=__import__("devmind.config", fromlist=["ModelConfig"]).ModelConfig(),
    )
    system = messages[0]["content"]
    assert "[C1]" in system
    assert "[M1]" in system
    assert "billing/refund.py" in system


def test_code_budget_is_enforced(tmp_path: Path) -> None:
    store, repo = build(tmp_path)
    retriever = Retriever(store, MemoryConfig(code_max_chars=200))
    context = retriever.context_for("refund_order capture_payment", repo=repo)
    assert len(context.block) < 1200
    assert len(context.code_hits) <= 2


def test_no_repo_means_no_code(tmp_path: Path) -> None:
    store, _ = build(tmp_path)
    retriever = Retriever(store, MemoryConfig())
    context = retriever.context_for("refund_order")
    assert context.code_hits == []
    assert "```" not in context.block
