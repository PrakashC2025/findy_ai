"""Verification before presentation.

Two paths, both free:

* **Local** - detect the project's own test command (pytest, npm test, cargo
  test, go test, make test), run it with a timeout and a scrubbed environment,
  and show the tail of the output.  This is how a "fixed" answer gets checked
  before you ever see it.
* **GitHub Actions** - for repos where the test suite only runs in CI, dispatch
  the workflow on your branch and read the result back. Actions minutes for
  public repos are free; private repos use your account's included minutes.

Process isolation lives in :mod:`devmind.sandbox` and is opt-in per call, so the
plain ``devmind verify`` path stays as fast as the test suite itself.  A green
run does not prove the code is correct - only that the checks the project
defines passed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .github import GitHubClient, GitHubError

#: Environment variables that must never leak into a test process.
_SECRET_ENV = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_PAT",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "GOOGLE_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "DEVMIND_PASSPHRASE",
)

OUTPUT_TAIL = 4000


@dataclass
class CheckSpec:
    name: str
    command: list[str]


@dataclass
class CheckResult:
    name: str
    command: list[str]
    ok: bool
    exit_code: int = -1
    output: str = ""
    duration_ms: float = 0.0
    skipped_reason: str = ""

    @property
    def skipped(self) -> bool:
        return bool(self.skipped_reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "duration_ms": round(self.duration_ms, 1),
            "skipped": self.skipped,
            "skipped_reason": self.skipped_reason,
            "output": self.output,
        }


@dataclass
class ActionsResult:
    ok: bool
    conclusion: str
    run_id: int | None = None
    url: str = ""
    detail: str = ""
    failed_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# --------------------------------------------------------------------- local


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _has_python_tests(root: Path) -> bool:
    if any(
        (root / name).exists()
        for name in (
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "pytest.ini",
            "tox.ini",
        )
    ):
        return True
    tests = root / "tests"
    if tests.is_dir():
        return any(tests.rglob("test_*.py")) or any(tests.rglob("*_test.py"))
    return any(root.rglob("test_*.py"))


def _node_test_command(root: Path) -> list[str] | None:
    package = root / "package.json"
    if not package.exists():
        return None
    try:
        payload = json.loads(_read(package) or "{}")
    except json.JSONDecodeError:
        return None
    script = (payload.get("scripts") or {}).get("test", "")
    if not script or "no test specified" in script:
        return None
    if shutil.which("npm") is None:
        return None
    return ["npm", "test", "--silent"]


def _make_test_command(root: Path) -> list[str] | None:
    makefile = root / "Makefile"
    if not makefile.exists() or shutil.which("make") is None:
        return None
    for line in _read(makefile).splitlines():
        if line.startswith("test:"):
            return ["make", "test"]
    return None


def detect_checks(root: str | Path) -> list[CheckSpec]:
    """Pick at most three honest, project-defined checks."""
    root = Path(root)
    specs: list[CheckSpec] = []

    if _has_python_tests(root):
        specs.append(CheckSpec("pytest", [sys.executable, "-m", "pytest", "-q"]))

    node = _node_test_command(root)
    if node:
        specs.append(CheckSpec("npm test", node))

    if (root / "Cargo.toml").exists() and shutil.which("cargo"):
        specs.append(CheckSpec("cargo test", ["cargo", "test", "--quiet"]))

    if (root / "go.mod").exists() and shutil.which("go"):
        specs.append(CheckSpec("go test", ["go", "test", "./..."]))

    make = _make_test_command(root)
    if make and not any(spec.name in {"pytest", "npm test"} for spec in specs):
        specs.append(CheckSpec("make test", make))

    # Deduplicate by command tuple, keep order, cap at three.
    seen: set[tuple[str, ...]] = set()
    unique: list[CheckSpec] = []
    for spec in specs:
        key = tuple(spec.command)
        if key not in seen:
            seen.add(key)
            unique.append(spec)
    return unique[:3]


def _clean_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _SECRET_ENV}
    env.update({"CI": "1", "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def run_check(
    spec: CheckSpec,
    root: str | Path,
    *,
    timeout: float = 600.0,
    sandbox: Any = None,
) -> CheckResult:
    """Run one check.  Pass a :class:`devmind.sandbox.Sandbox` to enforce limits.

    The sandbox is duck-typed to avoid a circular import: it exposes
    ``run_check(spec, root, timeout=...)`` and returns ``CheckResult``.
    """
    if sandbox is not None:
        return sandbox.run_check(spec, root, timeout=timeout)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            spec.command,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_clean_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            spec.name,
            spec.command,
            False,
            -1,
            f"timed out after {timeout:.0f}s",
            (time.perf_counter() - started) * 1000,
        )
    except (OSError, ValueError) as exc:
        return CheckResult(
            spec.name,
            spec.command,
            False,
            -1,
            f"could not run: {exc}",
            (time.perf_counter() - started) * 1000,
        )
    output = ((completed.stdout or "") + (completed.stderr or ""))[-OUTPUT_TAIL:]
    return CheckResult(
        name=spec.name,
        command=spec.command,
        ok=completed.returncode == 0,
        exit_code=completed.returncode,
        output=output,
        duration_ms=(time.perf_counter() - started) * 1000,
    )


def run_local_checks(
    root: str | Path,
    *,
    timeout: float = 600.0,
    checks: list[CheckSpec] | None = None,
    sandbox: Any = None,
) -> list[CheckResult]:
    root = Path(root)
    specs = detect_checks(root) if checks is None else checks
    if not specs:
        return [
            CheckResult(
                "none",
                [],
                ok=False,
                skipped_reason=(
                    "no test command detected (pytest / npm test / cargo test / go test / make test)"
                ),
            )
        ]
    return [run_check(spec, root, timeout=timeout, sandbox=sandbox) for spec in specs]


def summarise(results: list[CheckResult]) -> str:
    if not results:
        return "no checks ran"
    parts = []
    for result in results:
        if result.skipped:
            parts.append(f"{result.name}: skipped ({result.skipped_reason})")
        else:
            parts.append(f"{result.name}: {'pass' if result.ok else 'FAIL'}")
    return ", ".join(parts)


# ------------------------------------------------------------ GitHub Actions


class ActionsVerifier:
    """Dispatch a workflow on a branch and read the conclusion back."""

    def __init__(
        self,
        client: GitHubClient,
        *,
        poll_seconds: float = 10.0,
        sleep=time.sleep,
    ):
        self.client = client
        self.poll_seconds = poll_seconds
        self._sleep = sleep

    def trigger(self, repo: str, workflow: str, ref: str) -> None:
        try:
            self.client.dispatch_workflow(repo, workflow, ref)
        except GitHubError as exc:
            raise GitHubError(
                f"{exc}\n"
                "Check the workflow file (e.g. .github/workflows/test.yml), that it has "
                "`on: workflow_dispatch`, and that the branch exists on GitHub."
            ) from exc

    def wait(
        self,
        repo: str,
        *,
        branch: str,
        workflow: str | None = None,
        timeout: float = 900.0,
        started_after: float | None = None,
    ) -> ActionsResult:
        deadline = time.monotonic() + timeout
        cutoff = _iso(started_after if started_after is not None else time.time() - 120)
        last: dict[str, Any] | None = None

        while time.monotonic() < deadline:
            try:
                runs = self.client.workflow_runs(repo, branch=branch)
            except GitHubError as exc:
                return ActionsResult(False, "error", detail=str(exc))
            candidates = [
                run
                for run in runs
                if (not workflow or _matches(run, workflow))
                and str(run.get("created_at", "")) >= cutoff
            ]
            if candidates:
                last = max(candidates, key=lambda run: run.get("created_at", ""))
                if last.get("status") == "completed":
                    return self._result(repo, last)
            self._sleep(self.poll_seconds)

        if last is None:
            return ActionsResult(
                False,
                "timeout",
                detail=(
                    "no workflow run appeared for this branch. Is the workflow triggered "
                    "by `workflow_dispatch` or a push?"
                ),
            )
        return ActionsResult(
            False,
            str(last.get("status") or "timeout"),
            run_id=last.get("id"),
            url=str(last.get("html_url", "")),
            detail=f"still {last.get('status')} after {timeout:.0f}s",
        )

    def _result(self, repo: str, run: dict[str, Any]) -> ActionsResult:
        run_id = run.get("id")
        conclusion = str(run.get("conclusion") or "unknown")
        failed_steps: list[str] = []
        if run_id:
            try:
                for job in self.client.run_jobs(repo, int(run_id)):
                    if job.get("conclusion") in {"failure", "timed_out", "cancelled"}:
                        for step in job.get("steps", []):
                            if step.get("conclusion") == "failure":
                                failed_steps.append(f"{job.get('name')} / {step.get('name')}")
            except GitHubError:
                pass
        return ActionsResult(
            ok=conclusion == "success",
            conclusion=conclusion,
            run_id=run_id,
            url=str(run.get("html_url", "")),
            detail=str(run.get("name", "")),
            failed_steps=failed_steps,
        )


def _matches(run: dict[str, Any], workflow: str) -> bool:
    name = str(run.get("name", ""))
    path = str(run.get("path", ""))
    return workflow in {name, path} or path.endswith("/" + workflow) or path.endswith(workflow)


def _iso(timestamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))
