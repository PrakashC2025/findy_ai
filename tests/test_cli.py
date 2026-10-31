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


def test_ask_without_a_backend_is_actionable_not_a_traceback(tmp_home: Path, capsys) -> None:
    """Regression: async routing must not call asyncio.run() inside a loop."""
    code = main(["--home", str(tmp_home), "ask", "hello"])
    captured = capsys.readouterr()
    assert code in (1, 2)
    assert any(word in captured.err for word in ("llama.cpp", "Ollama", "pull"))
    assert "Traceback" not in captured.err


def write_project(root: Path) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "billing.py").write_text(
        "def refund_order(order_id):\n"
        '    """Refund a paid order exactly once."""\n'
        "    return order_id\n"
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_billing.py").write_text(
        "from pkg.billing import refund_order\n\n"
        "def test_refund():\n"
        "    assert refund_order(1) == 1\n"
    )
    return root


def test_index_search_and_repos(tmp_home: Path, tmp_path: Path, capsys) -> None:
    project = write_project(tmp_path / "shop")

    assert run(tmp_home, "index", str(project), "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["indexed"] >= 2
    assert report["chunks"] >= 2

    assert run(tmp_home, "search", "refund order", "--repo", "shop") == 0
    out = capsys.readouterr().out
    assert "pkg/billing.py" in out

    assert run(tmp_home, "repos", "--json") == 0
    repos = json.loads(capsys.readouterr().out)
    assert repos[0]["repo"] == "shop"
    assert repos[0]["files"] >= 2


def test_ask_with_repo_injects_code(tmp_home: Path, tmp_path: Path, capsys) -> None:
    project = write_project(tmp_path / "shop")
    assert (
        run(tmp_home, "ask", "--json", "--repo", str(project), "how does refund_order work?") == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["memory"]["code_hits"]
    assert any(citation["kind"] == "code" for citation in payload["memory"]["citations"])


def test_verify_local_runs_tests(tmp_home: Path, tmp_path: Path, capsys) -> None:
    project = write_project(tmp_path / "shop")
    (project / "pyproject.toml").write_text("[project]\nname='shop'\nversion='0.1.0'\n")
    code = run(tmp_home, "verify", str(project))
    out = capsys.readouterr().out
    assert code == 0
    assert "pytest" in out


def test_github_status_without_login(tmp_home: Path, capsys) -> None:
    assert run(tmp_home, "github", "status") == 1
    assert "Not logged in" in capsys.readouterr().out


def test_repo_context_is_automatic_only_inside_the_project(
    tmp_home: Path, tmp_path: Path, capsys, monkeypatch
) -> None:
    project = write_project(tmp_path / "shop")
    assert run(tmp_home, "index", str(project), "--json") == 0
    capsys.readouterr()

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert run(tmp_home, "ask", "--json", "how does refund work?") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["memory"]["code_hits"] == []

    monkeypatch.chdir(project)
    assert run(tmp_home, "ask", "--json", "how does refund work?") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["memory"]["code_hits"]


def test_check_runs_in_a_sandbox_and_reports_findings(
    tmp_home: Path, tmp_path: Path, capsys
) -> None:
    project = write_project(tmp_path / "shop")
    (project / "pyproject.toml").write_text("[project]\nname='shop'\nversion='0.1.0'\n")
    # devmind: allow code.eval
    (project / "pkg" / "unsafe.py").write_text("value = eval(input())\n")

    code = run(tmp_home, "check", str(project), "--json", "--timeout", "120")
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["checks"][0]["name"] == "pytest"
    assert payload["checks"][0]["ok"] is True
    assert any(item["rule"] == "code.eval" for item in payload["security"])
    assert payload["ok"] is False


def test_check_fails_when_the_suite_fails(tmp_home: Path, tmp_path: Path, capsys) -> None:
    project = write_project(tmp_path / "shop")
    (project / "pyproject.toml").write_text("[project]\nname='shop'\nversion='0.1.0'\n")
    (project / "tests" / "test_billing.py").write_text(
        "from pkg.billing import refund_order\n\ndef test_refund():\n    assert refund_order(1) == 2\n"
    )
    code = run(tmp_home, "check", str(project), "--json", "--timeout", "120")
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["checks"][0]["ok"] is False
    assert "assert" in payload["checks"][0]["output"]


def test_check_without_checks_is_honest(tmp_home: Path, tmp_path: Path, capsys) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run(tmp_home, "check", str(empty)) == 1
    captured = capsys.readouterr()
    assert "nothing is verified" in captured.out + captured.err


def test_scan_blocks_high_severity_and_json(tmp_home: Path, tmp_path: Path, capsys) -> None:
    project = tmp_path / "app"
    project.mkdir()
    # devmind: allow sql.interpolation, sql.dynamic
    (project / "db.py").write_text('cursor.execute(f"SELECT * FROM t WHERE id = {user_id}")\n')
    assert run(tmp_home, "scan", str(project), "--json") == 1
    findings = json.loads(capsys.readouterr().out)
    assert findings[0]["rule"] == "sql.interpolation"
    assert findings[0]["severity"] == "high"

    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "ok.py").write_text("print('hello')\n")
    assert run(tmp_home, "scan", str(clean)) == 0
    captured = capsys.readouterr()
    assert "clean" in captured.out + captured.err


def test_languages_lists_packs_for_a_ts_project(tmp_home: Path, tmp_path: Path, capsys) -> None:
    root = tmp_path / "web"
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({"dependencies": {"react": "^19.0.0"}}))
    (root / "tsconfig.json").write_text("{}")
    (root / "src" / "App.tsx").write_text("export const App = () => <div />;\n")

    assert run(tmp_home, "languages", "--root", str(root), "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(pack["id"] == "typescript" for pack in payload["packs"])

    assert run(tmp_home, "languages", "--root", str(root)) == 0
    out = capsys.readouterr().out
    assert "TypeScript" in out
    assert "tsc --noEmit" in out


def test_fix_without_checks_explains_instead_of_guessing(
    tmp_home: Path, tmp_path: Path, capsys
) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert run(tmp_home, "fix", "make it work", "--root", str(empty)) == 1
    captured = capsys.readouterr()
    assert "cannot verify a patch" in captured.out + captured.err


def test_fix_demo_mode_refuses_to_pretend(tmp_home: Path, tmp_path: Path, capsys) -> None:
    project = write_project(tmp_path / "shop")
    (project / "pyproject.toml").write_text("[project]\nname='shop'\nversion='0.1.0'\n")
    code = run(tmp_home, "fix", "the refund test fails", "--root", str(project))
    captured = capsys.readouterr()
    assert code == 1
    assert "demo mode has no model" in captured.out + captured.err
    # Nothing was written to the project.
    assert (project / "pkg" / "billing.py").read_text().count("refund_order") == 1


def test_ask_injects_language_pack_guidance(tmp_home: Path, tmp_path: Path, capsys) -> None:
    project = write_project(tmp_path / "shop")
    (project / "pyproject.toml").write_text("[project]\nname='shop'\nversion='0.1.0'\n")
    assert run(tmp_home, "ask", "--json", "--repo", str(project), "add a refund guard") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["language_packs"] == ["python"]
    assert payload["memory"]["code_hits"]


def test_brief_is_stored_shown_and_cleared(tmp_home: Path, capsys) -> None:
    assert run(tmp_home, "brief", "We sell furniture in Chennai; warm, plain English.") == 0
    capsys.readouterr()

    assert run(tmp_home, "brief") == 0
    assert "furniture" in capsys.readouterr().out

    store = MemoryStore(tmp_home / "memory.db")
    notes = [row["text"] for row in store.list_memories(kind="business")]
    assert notes and notes[0].startswith("We sell")
    store.close()

    assert run(tmp_home, "brief", "--clear") == 0
    capsys.readouterr()
    assert run(tmp_home, "brief") == 0
    captured = capsys.readouterr()
    assert "no business context yet" in captured.out + captured.err


def test_business_block_is_always_in_the_prompt(tmp_home: Path, capsys) -> None:
    assert run(tmp_home, "brief", "B2B; always mention delivery time.") == 0
    capsys.readouterr()
    assert run(tmp_home, "ask", "--json", "--show-prompt", "add a refund button") == 0
    payload = json.loads(capsys.readouterr().out)
    system = payload["messages"][0]["content"]
    assert "BUSINESS CONTEXT" in system
    assert "delivery time" in system
    assert payload["characters"] > 0


def test_new_scaffolds_a_project(tmp_home: Path, tmp_path: Path, capsys) -> None:
    target = tmp_path / "landing"
    assert run(tmp_home, "new", "website", str(target)) == 0
    out = capsys.readouterr().out
    assert (target / "index.html").exists()
    assert "next steps" in out
    assert "python3 -m http.server" in out

    assert run(tmp_home, "new", "website", str(target)) == 1
    assert "not empty" in capsys.readouterr().err
