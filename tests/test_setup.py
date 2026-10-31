"""`devmind setup` turns "Apache owns 8080" into a working config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devmind.cli import main
from devmind.config import Config
from devmind.ports import CLOSED, LLAMA, WEB, PortReport
from devmind.setup import SetupError, apply_plan, build_plan, find_gguf


def run(home: Path, *args: str) -> int:
    return main(["--home", str(home), *args])


APACHE = PortReport(8080, WEB, "HTTP 200 on / · Server: Apache · Apache web server")
FREE_8081 = PortReport(8081, CLOSED, "nothing is listening")
FREE_8082 = PortReport(8082, CLOSED, "nothing is listening")


def test_apache_on_8080_moves_to_a_free_port(tmp_path: Path) -> None:
    config = Config.load(tmp_path / "home")
    plan = build_plan(
        config,
        reports=[APACHE, FREE_8081, FREE_8082],
        has_llama_server=True,
        has_ollama=False,
    )
    assert plan.kind == "llamacpp"
    assert plan.port == 8081
    assert plan.base_url == "http://127.0.0.1:8081"
    assert "--port 8081" in plan.start_command
    assert any("8080" in note for note in plan.notes)


def test_an_existing_model_server_is_used_as_is(tmp_path: Path) -> None:
    config = Config.load(tmp_path / "home")
    llama = PortReport(8084, LLAMA, "1 model(s): qwen2.5-coder-7b")
    plan = build_plan(
        config, reports=[APACHE, FREE_8081, llama], has_llama_server=True, has_ollama=False
    )
    assert plan.port == 8084
    assert any("found llama.cpp" in note for note in plan.notes)


def test_explicit_port_wins(tmp_path: Path) -> None:
    config = Config.load(tmp_path / "home")
    plan = build_plan(
        config, reports=[APACHE, FREE_8081], port=9000, has_llama_server=True, has_ollama=False
    )
    assert plan.port == 9000
    assert plan.base_url == "http://127.0.0.1:9000"


def test_ollama_plan_keeps_its_own_port(tmp_path: Path) -> None:
    config = Config.load(tmp_path / "home")
    plan = build_plan(config, kind="ollama", reports=[APACHE], has_ollama=True)
    assert plan.kind == "ollama"
    assert plan.port == 11434
    assert plan.base_url == config.ollama.base_url


def test_no_free_port_is_an_error_not_a_crash(tmp_path: Path) -> None:
    config = Config.load(tmp_path / "home")
    taken = [PortReport(port, WEB, "HTTP 200 on /") for port in range(8080, 8083)]
    with pytest.raises(SetupError):
        build_plan(config, reports=taken, has_llama_server=True, has_ollama=False)


def test_apply_plan_writes_and_reloads(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = Config.load(home)
    plan = build_plan(config, reports=[APACHE, FREE_8081], has_llama_server=True, has_ollama=False)
    assert apply_plan(config, plan) is True

    reloaded = Config.load(home)
    assert reloaded.llamacpp.base_url == "http://127.0.0.1:8081"
    assert apply_plan(reloaded, plan) is False, "a second apply must not rewrite the file"


def test_find_gguf(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    (models / "qwen.gguf").write_text("x")
    assert find_gguf([models]).name == "qwen.gguf"
    assert find_gguf([tmp_path / "missing"]) is None


def test_cli_setup_json_uses_the_scan(tmp_home: Path, monkeypatch, capsys) -> None:
    from devmind import setup as setup_module

    monkeypatch.setattr(setup_module, "scan", lambda *args, **kwargs: [APACHE, FREE_8081])
    monkeypatch.setattr(setup_module.shutil, "which", lambda name: None)

    assert run(tmp_home, "setup", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["port"] == 8081
    assert payload["written"] is False
    assert payload["changed"] is True


def test_cli_setup_write_saves_the_config(tmp_home: Path, monkeypatch, capsys) -> None:
    from devmind import setup as setup_module

    monkeypatch.setattr(setup_module, "scan", lambda *args, **kwargs: [APACHE, FREE_8081])
    monkeypatch.setattr(setup_module.shutil, "which", lambda name: None)

    assert run(tmp_home, "setup", "--write") == 0
    capsys.readouterr()
    assert Config.load(tmp_home).llamacpp.base_url == "http://127.0.0.1:8081"
