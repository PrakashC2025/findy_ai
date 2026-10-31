"""The double-click launchers are the front door; they must fail loudly, not silently."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _run(script: Path, **env: str) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, **env}
    merged.pop("DEVMIND_HOME", None)
    return subprocess.run(
        ["bash", str(script)],
        cwd=ROOT,
        env=merged,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_shell_scripts_parse() -> None:
    for name in ("devmind.sh", "devmind.command"):
        script = SCRIPTS / name
        result = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "script",
    [
        SCRIPTS / "devmind.sh",
        SCRIPTS / "devmind.command",
        ROOT / "START-DEVMIND.command",
    ],
    ids=["sh", "command", "root-command"],
)
def test_dry_run_reports_what_it_would_do(script: Path) -> None:
    result = _run(script, DEVMIND_LAUNCH_DRY_RUN="1")
    assert result.returncode == 0, result.stderr
    assert "command=" in result.stdout
    assert "serve --open --port 8787" in result.stdout


def test_launchers_are_executable() -> None:
    for name in ("devmind.sh", "devmind.command", "../START-DEVMIND.command"):
        mode = (SCRIPTS / name).resolve().stat().st_mode
        assert mode & stat.S_IXUSR, f"{name} lost its executable bit"


def test_windows_launcher_is_windows_safe() -> None:
    raw = (SCRIPTS / "devmind.bat").read_bytes()
    text = raw.decode()
    assert b"\r\n" in raw, "cmd.exe is happier with CRLF line endings"
    assert raw.count(b"\n") == raw.count(b"\r\n"), "mixed line endings in a .bat"
    assert "python.org" in text, "a user without Python needs a link, not a stack trace"
    assert "pip install" in text
    assert "pause" in text, "the window must not vanish on failure"
    assert "py -3" in text
    assert "DEVMIND_LAUNCH_DRY_RUN" in text
    assert 'start "" "https://www.python.org/downloads/windows/"' in text


def test_root_launchers_delegate() -> None:
    assert "scripts\\devmind.bat" in (ROOT / "START-DEVMIND.bat").read_text()
    assert "scripts/devmind.sh" in (ROOT / "START-DEVMIND.command").read_text()


def test_windows_wrapper_runs_the_venv_without_path_changes() -> None:
    raw = (ROOT / "devmind.bat").read_bytes()
    text = raw.decode()
    assert raw.count(b"\n") == raw.count(b"\r\n")
    assert ".venv\\Scripts\\python.exe" in text
    assert "-m devmind %*" in text
    assert "START-DEVMIND.bat" in text, "a first-time user must be told how to create the venv"


def test_windows_guide_covers_the_missing_winget_case() -> None:
    guide = (ROOT / "docs" / "windows.md").read_text()
    assert "OllamaSetup.exe" in guide
    assert "does not exist on older Windows 10" in guide
    assert "curl.exe" in guide
    assert "ProgressPreference" in guide, "IWR's progress bar is why the download crawls"
    assert "Start-BitsTransfer" in guide
    assert "Apache" in guide
