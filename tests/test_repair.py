"""Self-repair: real diffs, real checks, real refusals."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from devmind.repair import SelfRepairer, apply_diff, extract_diff
from devmind.verify import CheckSpec


class ScriptedBackend:
    """Backend that replays a fixed list of replies, ignoring the prompt."""

    name = "scripted"

    def __init__(self, replies: list[str]):
        self.replies = replies
        self.calls = 0
        self.seen: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]], model: str, **_: Any):
        self.seen.append(messages)
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        yield reply

    async def aclose(self) -> None:  # pragma: no cover - protocol completeness
        return None


def _diff(old: str, new: str, path: str) -> str:
    return f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n"


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "answer.txt").write_text("0\n")
    (root / "app.py").write_text("value = 1\n")
    (root / "check.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "ok = Path('answer.txt').read_text().strip() == '42'\n"
        "print('ok' if ok else 'answer must be 42')\n"
        "sys.exit(0 if ok else 1)\n"
    )
    return root


def _checks() -> list[CheckSpec]:
    return [CheckSpec("answer", [sys.executable, "check.py"])]


def test_extract_diff_handles_fences_and_bare_diffs() -> None:
    fenced = "Here you go:\n```diff\n" + _diff("a", "b", "x.txt") + "```\nThat fixes it."
    assert extract_diff(fenced) == _diff("a", "b", "x.txt")
    bare = "Sure.\n" + _diff("a", "b", "x.txt")
    assert extract_diff(bare) == _diff("a", "b", "x.txt")
    assert extract_diff("```python\nprint(1)\n```") is None
    assert extract_diff("no code here") is None


def test_apply_diff_changes_files_and_reports_failures(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    target.write_text("one\n")
    ok, message = apply_diff(tmp_path, _diff("one", "two", "x.txt"))
    assert ok, message
    assert target.read_text() == "two\n"

    broken, detail = apply_diff(tmp_path, _diff("missing", "three", "x.txt"))
    assert not broken
    assert "failed" in detail


def test_first_round_fix_passes(tmp_path: Path) -> None:
    root = _project(tmp_path)
    backend = ScriptedBackend(["```diff\n" + _diff("0", "42", "answer.txt") + "```"])
    outcome = run(backend, root, max_rounds=3)
    assert outcome.ok
    assert outcome.rounds == 1
    assert outcome.diff.strip().startswith("--- a/answer.txt")
    assert "verified in round 1" in outcome.summary
    assert outcome.confidence.score >= 0.5
    # The real project is untouched until --apply.
    assert (root / "answer.txt").read_text() == "0\n"


def test_second_round_recovers_after_a_failing_patch(tmp_path: Path) -> None:
    root = _project(tmp_path)
    backend = ScriptedBackend(
        [
            "```diff\n" + _diff("0", "41", "answer.txt") + "```",
            "```diff\n" + _diff("0", "42", "answer.txt") + "```",
        ]
    )
    outcome = run(backend, root, max_rounds=3)
    assert outcome.ok
    assert outcome.rounds == 2
    assert len(outcome.attempts) == 2
    assert not outcome.attempts[0].ok
    assert outcome.attempts[1].ok
    # The second prompt must contain the real failure output.
    assert "answer must be 42" in backend.seen[-1][-1]["content"]


def test_gives_up_honestly_after_the_cap(tmp_path: Path) -> None:
    root = _project(tmp_path)
    backend = ScriptedBackend(["```diff\n" + _diff("0", "41", "answer.txt") + "```"])
    outcome = run(backend, root, max_rounds=2)
    assert not outcome.ok
    assert outcome.rounds == 2
    assert backend.calls == 2
    assert "not fixed after 2 round(s)" in outcome.summary
    assert "answer" in outcome.summary


def test_security_scan_blocks_dangerous_patches(tmp_path: Path) -> None:
    root = _project(tmp_path)
    # devmind: allow code.eval
    evil = "--- a/app.py\n+++ b/app.py\n@@ -1 +1,2 @@\n value = 1\n+result = eval(payload)\n"
    backend = ScriptedBackend(["```diff\n" + evil + "```"])
    outcome = run(backend, root, max_rounds=1)
    assert not outcome.ok
    assert outcome.attempts[0].security
    assert "security scan" in outcome.attempts[0].error
    assert "value = 1\n" in (root / "app.py").read_text()


def test_clean_patch_is_not_blocked(tmp_path: Path) -> None:
    root = _project(tmp_path)
    backend = ScriptedBackend(["```diff\n" + _diff("0", "42", "answer.txt") + "```"])
    outcome = run(backend, root, max_rounds=1)
    assert outcome.attempts[0].applied
    assert outcome.attempts[0].security == []
    assert outcome.ok


def test_no_checks_means_no_claims(tmp_path: Path) -> None:
    root = _project(tmp_path)
    outcome = run(ScriptedBackend(["no"]), root, checks=[], max_rounds=2)
    assert not outcome.ok
    assert outcome.rounds == 0
    assert "no test, lint or build command" in outcome.summary


def test_non_diff_reply_is_reported(tmp_path: Path) -> None:
    root = _project(tmp_path)
    backend = ScriptedBackend(["Here is the fix: just change the file."])
    outcome = run(backend, root, max_rounds=1)
    assert not outcome.ok
    assert "without a unified diff" in outcome.attempts[0].error


def test_cannot_fix_is_surfaced(tmp_path: Path) -> None:
    root = _project(tmp_path)
    backend = ScriptedBackend(["CANNOT FIX: the test file is missing"])
    outcome = run(backend, root, max_rounds=1)
    assert not outcome.ok
    assert "cannot fix" in outcome.attempts[0].error.lower()


def run(backend: Any, root: Path, *, max_rounds: int = 3, checks: Any = None) -> Any:
    """Small synchronous wrapper so tests do not need pytest-asyncio."""

    import asyncio

    repairer = SelfRepairer(
        backend,
        model="fake",
        root=root,
        checks=_checks() if checks is None else checks,
        max_rounds=max_rounds,
        timeout=30,
    )
    return asyncio.run(repairer.run("make answer.txt say 42"))
