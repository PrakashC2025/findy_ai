"""Sandbox behaviour: copies, limits, timeouts, honest capability reporting."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from devmind.sandbox import Sandbox, SandboxLimits, scan_protections
from devmind.verify import CheckSpec


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('hi')\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    (root / "node_modules" / "left-pad").mkdir(parents=True)
    (root / "node_modules" / "left-pad" / "index.js").write_text("module.exports=1")
    (root / ".env").write_text("TOKEN=super-secret")
    (root / ".venv").mkdir()
    (root / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")
    return root


def test_copy_excludes_vcs_deps_and_env_files(tmp_path: Path) -> None:
    root = _project(tmp_path)
    copy = Sandbox().copy_project(root)
    try:
        assert (copy / "src" / "app.py").exists()
        assert not (copy / ".git").exists()
        assert not (copy / "node_modules").exists()
        assert not (copy / ".venv").exists()
        assert not (copy / ".env").exists()
    finally:
        Sandbox.cleanup(copy)
    assert not copy.exists()


def test_run_captures_output_and_exit_code(tmp_path: Path) -> None:
    sandbox = Sandbox(SandboxLimits(timeout_s=10))
    ok = sandbox.run([sys.executable, "-c", "print('hello')"], tmp_path)
    assert ok.ok and ok.exit_code == 0 and "hello" in ok.output

    bad = sandbox.run([sys.executable, "-c", "import sys; sys.exit(3)"], tmp_path)
    assert not bad.ok and bad.exit_code == 3


def test_timeout_kills_the_process(tmp_path: Path) -> None:
    sandbox = Sandbox(SandboxLimits(timeout_s=0.3))
    result = sandbox.run([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path)
    assert not result.ok
    assert result.timed_out
    assert "timed out" in result.output


def test_secrets_from_the_host_are_not_visible(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEVMIND_TEST_TOKEN", "do-not-leak")
    sandbox = Sandbox(SandboxLimits(timeout_s=10))
    result = sandbox.run(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ.get('DEVMIND_TEST_TOKEN')); print(os.environ['HOME'])",
        ],
        tmp_path,
    )
    assert result.ok
    lines = result.output.strip().splitlines()
    assert lines[0] == "None"
    assert ".sandbox-home" not in lines[1]
    import os

    assert lines[1] != os.environ.get("HOME")


def test_memory_limit_is_enforced(tmp_path: Path) -> None:
    sandbox = Sandbox(SandboxLimits(timeout_s=20, memory_mb=256))
    result = sandbox.run(
        [sys.executable, "-c", "b = bytearray(400 * 1024 * 1024); print('allocated')"],
        tmp_path,
    )
    if result.ok:
        pytest.skip("kernel did not enforce RLIMIT_AS in this environment")
    assert "MemoryError" in result.output or result.exit_code != 0


def test_protections_are_reported_honestly() -> None:
    labels = scan_protections(SandboxLimits())
    assert any(label.startswith("memory") for label in labels)
    assert any(label.startswith("network:") for label in labels)
    assert any(label.startswith("filesystem:") for label in labels)
    # The report must say which case it is, never imply isolation unconditionally.
    network = next(label for label in labels if label.startswith("network:"))
    assert network in {
        "network: isolated (empty netns)",
        "network: not isolated (unshare unavailable)",
    } or ("not isolated" in network)


def test_run_check_matches_verify_result(tmp_path: Path) -> None:
    sandbox = Sandbox(SandboxLimits(timeout_s=10))
    ok = sandbox.run_check(CheckSpec("ok", [sys.executable, "-c", "print(1)"]), tmp_path)
    assert ok.ok and ok.name == "ok"
    bad = sandbox.run_check(
        CheckSpec("bad", [sys.executable, "-c", "raise SystemExit(2)"]), tmp_path
    )
    assert not bad.ok and bad.exit_code == 2


def test_run_in_copy_leaves_the_original_untouched(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "data.txt").write_text("original\n")

    def mutate(copy: Path) -> None:
        (copy / "data.txt").write_text("changed\n")

    sandbox = Sandbox(SandboxLimits(timeout_s=10))
    result = sandbox.run_in_copy(
        root, [sys.executable, "-c", "print(open('data.txt').read().strip())"], apply=mutate
    )
    assert result.ok and "changed" in result.output
    assert (root / "data.txt").read_text() == "original\n"
