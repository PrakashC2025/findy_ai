"""Self-repair: let the model fix its own patch until the checks pass.

The loop is deliberately narrow:

1. ask for a **unified diff** only, never prose or whole files;
2. apply it to a throwaway copy of the project (never the real tree);
3. reject the patch if the security scan flags high-severity code;
4. run the project's own checks;
5. on failure, send the failing output back and repeat, at most ``max_rounds``;
6. after the last round, report plainly what still fails and why.

Nothing is applied to the user's tree here.  ``devmind fix --apply`` does that
after the loop reports success, and then re-runs the checks on the real tree.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .confidence import Confidence, assess
from .sandbox import Sandbox, SandboxLimits
from .security_scan import Finding, blocking, scan_diff
from .verify import CheckResult, CheckSpec, detect_checks, run_check, summarise

_FAILURE_TAIL = 2500
_DIFF_FENCE = re.compile(r"```(?:diff|patch)?[ \t]*\n(.*?)```", re.S)

REPAIR_SYSTEM = """You are DevMind in repair mode. A project's own checks are failing.
You will receive the failing output, the relevant code, and (after the first round) the
patch you proposed last time.

Rules:
- Reply with ONE fenced ```diff block containing a unified diff and nothing else.
- The diff must apply with `git apply` from the project root.
- Change the minimum needed. Keep the project's naming, style and libraries.
- Do not add dependencies, do not edit tests to hide the failure, and do not
  rewrite files the failure does not touch.
- If you cannot fix it from the information given, reply with the single line
  `CANNOT FIX: <what you would need to know>` and no diff.
- Never invent a file path, API, flag or version that is not in the context.
"""


@dataclass
class Attempt:
    round_no: int
    diff: str
    applied: bool
    checks: list[CheckResult] = field(default_factory=list)
    error: str = ""
    security: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        if not self.applied or self.error or self.security:
            return False
        ran = [check for check in self.checks if not check.skipped]
        return bool(ran) and all(check.ok for check in ran)

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round_no,
            "applied": self.applied,
            "ok": self.ok,
            "error": self.error,
            "security": [finding.to_dict() for finding in self.security],
            "checks": [check.to_dict() for check in self.checks],
            "diff": self.diff,
        }


@dataclass
class RepairOutcome:
    ok: bool
    rounds: int
    diff: str
    attempts: list[Attempt]
    summary: str
    confidence: Confidence
    root: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rounds": self.rounds,
            "diff": self.diff,
            "summary": self.summary,
            "confidence": self.confidence.to_dict(),
            "root": self.root,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


def extract_diff(text: str) -> str | None:
    """Pull a unified diff out of a model reply, fenced or bare."""
    for block in _DIFF_FENCE.findall(text):
        if "--- " in block or "+++ " in block or block.lstrip().startswith("diff --git"):
            return block.strip() + "\n"
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(("diff --git", "--- a/")):
            return "\n".join(lines[index:]).strip() + "\n"
    return None


def _git_env() -> dict[str, str]:
    import os

    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "GIT_TERMINAL_PROMPT": "0",
    }


def apply_diff(root: str | Path, diff: str) -> tuple[bool, str]:
    """Apply a unified diff with ``git apply``; returns (ok, message)."""
    try:
        completed = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=str(root),
            input=diff,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
            env=_git_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run git apply: {exc}"
    if completed.returncode == 0:
        return True, "applied"
    detail = (completed.stderr or completed.stdout or "").strip()[:800]
    return False, f"git apply failed: {detail}"


def _failure_text(checks: list[CheckResult]) -> str:
    parts: list[str] = []
    for check in checks:
        if check.skipped:
            parts.append(f"[{check.name}] skipped: {check.skipped_reason}")
        elif not check.ok:
            parts.append(f"[{check.name}] exit {check.exit_code}\n{check.output[-_FAILURE_TAIL:]}")
    return "\n\n".join(parts)


def _best(attempts: list[Attempt]) -> Attempt | None:
    applied = [attempt for attempt in attempts if attempt.applied and not attempt.security]
    if not applied:
        return attempts[-1] if attempts else None

    def score(attempt: Attempt) -> tuple[int, int]:
        ran = [check for check in attempt.checks if not check.skipped]
        return (sum(1 for check in ran if check.ok), attempt.round_no)

    return max(applied, key=score)


class SelfRepairer:
    """Drive a model through at most ``max_rounds`` verified repair attempts."""

    def __init__(
        self,
        backend: Any,
        *,
        model: str,
        root: str | Path,
        checks: list[CheckSpec] | None = None,
        max_rounds: int = 3,
        timeout: float = 300.0,
        sandbox: Sandbox | None = None,
        options: dict[str, Any] | None = None,
    ):
        self.backend = backend
        self.model = model
        self.root = Path(root).resolve()
        self.checks = checks if checks is not None else detect_checks(self.root)
        self.max_rounds = max(1, min(5, int(max_rounds)))
        self.timeout = timeout
        self.sandbox = sandbox or Sandbox(SandboxLimits(timeout_s=timeout))
        self.options = options or {}

    async def run(
        self, prompt: str, *, context_block: str = "", language: str = ""
    ) -> RepairOutcome:
        if not self.checks:
            return RepairOutcome(
                ok=False,
                rounds=0,
                diff="",
                attempts=[],
                summary=(
                    "no test, lint or build command detected in this project - DevMind will not "
                    "claim a fix is verified when it cannot run anything"
                ),
                confidence=assess("", check_results=[], rounds=0),
                root=str(self.root),
            )

        system = REPAIR_SYSTEM
        if context_block.strip():
            system += "\n\n" + context_block.strip()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt.strip()},
        ]
        attempts: list[Attempt] = []

        for round_no in range(1, self.max_rounds + 1):
            reply = await self._ask(messages)
            diff = extract_diff(reply)
            if diff is None:
                reason = "model replied without a unified diff"
                if "CANNOT FIX" in reply.upper():
                    reason = "model reported it cannot fix this: " + reply.strip()[:300]
                attempts.append(Attempt(round_no, reply.strip()[:400], False, error=reason))
                messages += [
                    {"role": "assistant", "content": reply.strip()[:2000]},
                    {
                        "role": "user",
                        "content": (
                            "That was not a unified diff. Reply with ONE fenced ```diff block "
                            "and nothing else, or `CANNOT FIX: <reason>`."
                        ),
                    },
                ]
                continue

            attempt = self._evaluate(round_no, diff, language)
            attempts.append(attempt)
            if attempt.ok:
                return self._outcome(attempts, round_no, success=True, model_reply=reply)

            feedback = _failure_text(attempt.checks) or attempt.error
            messages += [
                {"role": "assistant", "content": "```diff\n" + diff.strip() + "\n```"},
                {
                    "role": "user",
                    "content": (
                        f"Round {round_no} failed.\n\n{feedback[: _FAILURE_TAIL * 2]}\n\n"
                        "Return a corrected unified diff that applies cleanly to the original "
                        "project (not to your previous patch). Change only what is necessary."
                    ),
                },
            ]

        return self._outcome(attempts, self.max_rounds, success=False, model_reply="")

    async def _ask(self, messages: list[dict[str, str]]) -> str:
        pieces: list[str] = []
        async for chunk in self.backend.chat(messages, self.model, options=self.options):
            pieces.append(chunk)
        return "".join(pieces)

    def _evaluate(self, round_no: int, diff: str, language: str) -> Attempt:
        attempt = Attempt(round_no=round_no, diff=diff, applied=False)
        attempt.security = blocking(scan_diff(diff, language=language))
        if attempt.security:
            rules = ", ".join(sorted({finding.rule for finding in attempt.security}))
            attempt.error = f"blocked by the security scan: {rules}"
            return attempt

        copy = self.sandbox.copy_project(self.root)
        try:
            applied, message = apply_diff(copy, diff)
            attempt.applied = applied
            if not applied:
                attempt.error = message
                return attempt
            attempt.checks = [
                run_check(spec, copy, timeout=self.timeout, sandbox=self.sandbox)
                for spec in self.checks
            ]
        finally:
            self.sandbox.cleanup(copy)
        return attempt

    def _outcome(
        self, attempts: list[Attempt], rounds: int, *, success: bool, model_reply: str
    ) -> RepairOutcome:
        best = _best(attempts) or Attempt(0, "", False)
        diff = best.diff if best.applied else ""
        if success:
            summary = f"verified in round {rounds}: {summarise(best.checks)}"
        else:
            failing = [check.name for check in best.checks if not check.skipped and not check.ok]
            summary = (
                f"not fixed after {rounds} round(s). "
                + (f"still failing: {', '.join(failing)}. " if failing else "")
                + (best.error or "no patch passed the checks")
            )
        confidence = assess(
            model_reply or summary,
            check_results=best.checks,
            rounds=rounds if success else 0,
        )
        return RepairOutcome(
            ok=success,
            rounds=rounds,
            diff=diff,
            attempts=attempts,
            summary=summary,
            confidence=confidence,
            root=str(self.root),
        )
