from __future__ import annotations

import time
from pathlib import Path

from devmind.verify import (
    ActionsVerifier,
    CheckSpec,
    detect_checks,
    run_check,
    run_local_checks,
    summarise,
)


def make_python_project(tmp_path: Path, *, failing: bool = False) -> Path:
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='proj'\nversion='0.1.0'\n")
    body = "assert True\n" if not failing else "assert 1 + 1 == 3\n"
    (root / "tests" / "test_sample.py").write_text("def test_math():\n    " + body)
    return root


def test_detect_pytest_command(tmp_path: Path) -> None:
    root = make_python_project(tmp_path)
    specs = detect_checks(root)
    assert specs
    assert specs[0].name == "pytest"
    assert specs[0].command[-2:] == ["pytest", "-q"]


def test_local_run_passes_and_fails(tmp_path: Path) -> None:
    passing = run_local_checks(make_python_project(tmp_path / "a"))
    assert passing[0].ok is True
    assert passing[0].exit_code == 0

    failing = run_local_checks(make_python_project(tmp_path / "b", failing=True))
    assert failing[0].ok is False
    assert failing[0].exit_code != 0
    assert "assert" in failing[0].output.lower()


def test_no_checks_is_reported_honestly(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    results = run_local_checks(empty)
    assert results[0].skipped is True
    assert "no test command" in results[0].skipped_reason


def test_timeout_is_bounded(tmp_path: Path) -> None:
    root = tmp_path / "slow"
    root.mkdir()
    result = run_check(
        CheckSpec("sleep", ["python3", "-c", "import time; time.sleep(5)"]),
        root,
        timeout=0.2,
    )
    assert result.ok is False
    assert "timed out" in result.output


def test_summary_text(tmp_path: Path) -> None:
    results = run_local_checks(make_python_project(tmp_path))
    assert "pytest: pass" in summarise(results)


class FakeClient:
    def __init__(self, runs, jobs=None, fail_jobs=False):
        self.runs = runs
        self.jobs = jobs or []
        self.fail_jobs = fail_jobs
        self.dispatched: list[tuple] = []

    def workflow_runs(self, repo, *, branch=None, per_page=10):
        return self.runs

    def run_jobs(self, repo, run_id):
        return self.jobs

    def dispatch_workflow(self, repo, workflow, ref, inputs=None):
        self.dispatched.append((repo, workflow, ref))
        return 204


def run_payload(conclusion: str, status: str = "completed", run_id: int = 1) -> dict:
    return {
        "id": run_id,
        "status": status,
        "conclusion": conclusion,
        "html_url": "https://github.com/me/app/actions/runs/1",
        "name": "CI",
        "path": ".github/workflows/test.yml",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def test_actions_success() -> None:
    client = FakeClient([run_payload("success")])
    result = ActionsVerifier(client, sleep=lambda seconds: None).wait(
        "me/app", branch="devmind/fix", workflow="test.yml"
    )
    assert result.ok is True
    assert result.conclusion == "success"
    assert result.run_id == 1


def test_actions_failure_lists_steps() -> None:
    jobs = [
        {
            "name": "test",
            "conclusion": "failure",
            "steps": [
                {"name": "run tests", "conclusion": "failure"},
                {"name": "checkout", "conclusion": "success"},
            ],
        }
    ]
    client = FakeClient([run_payload("failure")], jobs=jobs)
    result = ActionsVerifier(client, sleep=lambda seconds: None).wait(
        "me/app", branch="devmind/fix", workflow="test.yml"
    )
    assert result.ok is False
    assert result.failed_steps == ["test / run tests"]


def test_actions_timeout_without_runs() -> None:
    client = FakeClient([])
    result = ActionsVerifier(client, sleep=lambda seconds: None).wait(
        "me/app", branch="devmind/fix", timeout=0.05
    )
    assert result.ok is False
    assert result.conclusion == "timeout"


def test_dispatch_is_recorded() -> None:
    client = FakeClient([])
    ActionsVerifier(client, sleep=lambda seconds: None).trigger("me/app", "test.yml", "devmind/fix")
    assert client.dispatched == [("me/app", "test.yml", "devmind/fix")]
