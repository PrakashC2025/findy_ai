from __future__ import annotations

import json
from pathlib import Path

from devmind.cli import main
from devmind.memory.store import MemoryStore


def run(home: Path, *args: str) -> int:
    return main(["--home", str(home), "--demo", *args])


def test_doctor_reports_environment(tmp_home: Path, capsys) -> None:
    code = run(tmp_home, "doctor")
    out = capsys.readouterr().out
    assert "DevMind" in out
    assert "Model budget" in out
    assert "Privacy" in out
    assert code in (0, 1)  # 1 when this machine has no Ollama / little RAM


def test_models_json_is_machine_readable(tmp_home: Path, capsys) -> None:
    assert run(tmp_home, "models", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert "hardware" in payload and "models" in payload
    assert len(payload["models"]) >= 15
    assert any(model["installed"] for model in payload["models"]) is False
    assert all("fits" in model for model in payload["models"])


def test_models_table_mentions_recommendations(tmp_home: Path, capsys) -> None:
    assert run(tmp_home, "models") == 0
    out = capsys.readouterr().out
    assert "recommended" in out or "Suggested downloads" in out


def test_teach_then_list_memory(tmp_home: Path, capsys) -> None:
    assert run(tmp_home, "teach", "We use pnpm, never npm") == 0
    capsys.readouterr()
    assert run(tmp_home, "memory") == 0
    out = capsys.readouterr().out
    assert "pnpm" in out

    store = MemoryStore(tmp_home / "memory.db")
    assert store.memory_count() == 1


def test_ask_demo_returns_structured_answer(tmp_home: Path, capsys) -> None:
    assert run(tmp_home, "ask", "--json", "write a python slugify function") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["demo"] is True
    assert "Demo mode" in payload["answer"]
    assert payload["route"]["model"]
    assert payload["timings"]["total_ms"] >= 0


def test_unknown_model_fails_cleanly(tmp_home: Path, capsys) -> None:
    code = run(tmp_home, "ask", "--model", "made-up:1b", "hello")
    captured = capsys.readouterr()
    assert code == 1
    assert "Unknown model" in captured.err


def test_forget_all(tmp_home: Path, capsys) -> None:
    run(tmp_home, "teach", "Use pytest")
    capsys.readouterr()
    assert run(tmp_home, "forget", "--all", "--yes") == 0
    store = MemoryStore(tmp_home / "memory.db")
    assert store.memory_count() == 0


def test_stats_output(tmp_home: Path, capsys) -> None:
    run(tmp_home, "ask", "hello", "--json")
    capsys.readouterr()
    assert run(tmp_home, "stats") == 0
    out = capsys.readouterr().out
    assert "memories" in out
    assert "Recent routing decisions" in out


def test_no_command_prints_help(tmp_home: Path, capsys) -> None:
    assert main(["--home", str(tmp_home)]) == 0
    assert "usage: devmind" in capsys.readouterr().out


def test_ask_without_ollama_is_actionable_not_a_traceback(tmp_home: Path, capsys) -> None:
    """Regression: async routing must not call asyncio.run() inside a loop."""
    code = main(["--home", str(tmp_home), "ask", "hello"])
    captured = capsys.readouterr()
    assert code == 2
    assert "Ollama" in captured.err or "pull" in captured.err
    assert "Traceback" not in captured.err
