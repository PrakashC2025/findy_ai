"""Run a project's commands with real, enforced limits.

This is *process* isolation, not a container.  It stops the realistic
accidents - a runaway loop, a test that eats all the RAM, a script that writes
gigabytes, a command that tries to reach the network - and it runs against a
throwaway copy of the project so a half-finished patch can never touch the
user's real tree.

What it does **not** do: stop a determined attacker.  There is no seccomp
filter and no user namespace, so a hostile binary could still reach the
filesystem outside the copy.  Linux gets an optional network namespace when
``unshare`` is usable; macOS and Windows get no network isolation and the
result says so instead of pretending.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .verify import CheckResult, CheckSpec

#: Directories that are large, generated, or leak credentials from the host.
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".next",
    ".nuxt",
    "dist",
    "build",
    "target",
    "coverage",
    "out",
    ".tox",
    ".cache",
}

#: Files above this size are not copied into the sandbox.
_MAX_COPY_BYTES = 2_000_000
_MAX_OUTPUT = 6000

#: Only these host variables reach a sandboxed command.
_ENV_ALLOW = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "VIRTUAL_ENV", "SYSTEMROOT")

_UNSHARE: bool | None = None


@dataclass(frozen=True)
class SandboxLimits:
    timeout_s: float = 120.0
    cpu_seconds: int = 120
    memory_mb: int = 2048
    file_size_mb: int = 64
    max_processes: int = 64
    isolate_network: bool = True

    def protections(self) -> list[str]:
        return [
            f"time {self.timeout_s:.0f}s",
            f"cpu {self.cpu_seconds}s",
            f"memory {self.memory_mb}MB",
            f"file writes {self.file_size_mb}MB",
            f"processes {self.max_processes}",
        ]


@dataclass
class SandboxResult:
    command: list[str]
    ok: bool
    exit_code: int = -1
    output: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False
    protections: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "duration_ms": round(self.duration_ms, 1),
            "timed_out": self.timed_out,
            "protections": self.protections,
            "output": self.output,
        }


def _unshare_available() -> bool:
    """Can this machine put a child process in an empty network namespace?"""
    global _UNSHARE
    if _UNSHARE is None:
        _UNSHARE = False
        if os.name == "posix" and sys.platform.startswith("linux") and shutil.which("unshare"):
            try:
                probe = subprocess.run(
                    ["unshare", "-n", "--", "true"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                _UNSHARE = probe.returncode == 0
            except (OSError, subprocess.SubprocessError):
                _UNSHARE = False
    return _UNSHARE


def _limit_child(limits: SandboxLimits):  # pragma: no cover - runs in the child
    def _apply() -> None:
        if os.name != "posix":
            return
        import resource

        memory = limits.memory_mb * 1024 * 1024
        file_size = limits.file_size_mb * 1024 * 1024
        for name, value in (
            ("RLIMIT_CPU", limits.cpu_seconds),
            ("RLIMIT_AS", memory),
            ("RLIMIT_DATA", memory),
            ("RLIMIT_FSIZE", file_size),
            ("RLIMIT_NPROC", limits.max_processes),
            ("RLIMIT_CORE", 0),
        ):
            limit = getattr(resource, name, None)
            if limit is None:
                continue
            try:
                _soft, hard = resource.getrlimit(limit)
                ceiling = value if hard == resource.RLIM_INFINITY else min(value, hard)
                resource.setrlimit(limit, (ceiling, ceiling))
            except (OSError, ValueError):
                # Some kernels refuse RLIMIT_NPROC/AS for root; keep going.
                continue

    return _apply


def scan_protections(limits: SandboxLimits) -> list[str]:
    """What is actually enforced here, in plain words, for the UI."""
    out = limits.protections()
    if os.name != "posix":
        out.append("network: not isolated (POSIX limits unavailable)")
    elif limits.isolate_network and _unshare_available():
        out.append("network: isolated (empty netns)")
    else:
        out.append("network: not isolated (unshare unavailable)")
    out.append("filesystem: throwaway copy")
    return out


class Sandbox:
    """Create throwaway project copies and run commands inside the limits."""

    def __init__(self, limits: SandboxLimits | None = None):
        self.limits = limits or SandboxLimits()

    # ------------------------------------------------------------- copies
    def copy_project(self, root: str | Path) -> Path:
        """Copy source files into a temp dir; secrets and build outputs stay out."""
        source = Path(root).resolve()
        destination = Path(tempfile.mkdtemp(prefix="devmind-sandbox-"))

        def _ignore(directory: str, names: list[str]) -> set[str]:
            skipped: set[str] = set()
            for name in names:
                path = Path(directory) / name
                try:
                    too_big = path.is_file() and path.stat().st_size > _MAX_COPY_BYTES
                except OSError:
                    too_big = True
                if name in _SKIP_DIRS or name.startswith(".env") or path.is_symlink() or too_big:
                    skipped.add(name)
            return skipped

        shutil.copytree(source, destination, dirs_exist_ok=True, ignore=_ignore, symlinks=False)
        return destination

    @staticmethod
    def cleanup(path: str | Path) -> None:
        shutil.rmtree(path, ignore_errors=True)

    # --------------------------------------------------------------- runs
    def _environment(self, home: Path) -> dict[str, str]:
        """A clean HOME/TMP outside the project, so nothing is written into it."""
        env = {key: os.environ[key] for key in _ENV_ALLOW if key in os.environ}
        tmp = home / "tmp"
        tmp.mkdir(exist_ok=True)
        env.update(
            {
                "HOME": str(home),
                "TMPDIR": str(tmp),
                "TMP": str(tmp),
                "TEMP": str(tmp),
                "CI": "1",
                "TERM": "dumb",
                "GIT_TERMINAL_PROMPT": "0",
                "PYTHONDONTWRITEBYTECODE": "1",
                # Keep pytest's cache out of the user's tree.
                "PYTEST_ADDOPTS": "-p no:cacheprovider",
                "DEVMIND_SANDBOX": "1",
            }
        )
        return env

    def run(
        self,
        command: list[str],
        cwd: str | Path,
        *,
        timeout: float | None = None,
        isolate_network: bool | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> SandboxResult:
        workdir = Path(cwd).resolve()
        limit_s = self.limits.timeout_s if timeout is None else timeout
        network = self.limits.isolate_network if isolate_network is None else isolate_network
        prefix = ["unshare", "-n", "--"] if network and _unshare_available() else []
        home = Path(tempfile.mkdtemp(prefix="devmind-home-"))
        env = self._environment(home)
        if env_extra:
            env.update(env_extra)
        started = time.perf_counter()
        protections = scan_protections(self.limits)
        try:
            proc = subprocess.Popen(
                [*prefix, *command],
                cwd=str(workdir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=os.name == "posix",
                preexec_fn=_limit_child(self.limits) if os.name == "posix" else None,
            )
        except (OSError, ValueError) as exc:
            shutil.rmtree(home, ignore_errors=True)
            return SandboxResult(
                command, False, -1, f"could not run: {exc}", 0.0, False, protections
            )

        timed_out = False
        try:
            raw, _ = proc.communicate(timeout=limit_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._kill(proc)
            raw, _ = proc.communicate()
        shutil.rmtree(home, ignore_errors=True)
        output = (raw or "")[-_MAX_OUTPUT:]
        code = proc.returncode if proc.returncode is not None else -1
        if timed_out:
            output = f"timed out after {limit_s:.0f}s\n{output}".strip()
        return SandboxResult(
            command=command,
            ok=(not timed_out and code == 0),
            exit_code=code,
            output=output,
            duration_ms=(time.perf_counter() - started) * 1000,
            timed_out=timed_out,
            protections=protections,
        )

    @staticmethod
    def _kill(proc: subprocess.Popen[str]) -> None:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:  # pragma: no cover - non-POSIX fallback
                proc.kill()
        except (OSError, ProcessLookupError):
            proc.kill()

    # ------------------------------------------------------------ checks
    def run_check(
        self, spec: CheckSpec, root: str | Path, *, timeout: float | None = None
    ) -> CheckResult:
        """Run one detected check inside the limits and keep the same result type."""
        result = self.run(spec.command, root, timeout=timeout)
        return CheckResult(
            name=spec.name,
            command=spec.command,
            ok=result.ok,
            exit_code=result.exit_code,
            output=result.output,
            duration_ms=result.duration_ms,
        )

    def run_in_copy(
        self,
        root: str | Path,
        command: list[str],
        *,
        apply: Any = None,
        timeout: float | None = None,
    ) -> SandboxResult:
        """Copy, optionally mutate the copy, run once, then delete the copy."""
        copy = self.copy_project(root)
        try:
            if apply is not None:
                apply(copy)
            return self.run(command, copy, timeout=timeout)
        finally:
            self.cleanup(copy)
