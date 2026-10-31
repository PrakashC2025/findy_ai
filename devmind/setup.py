"""Work out a configuration that points at a real model, from what is on this machine.

This exists because "edit [llamacpp] base_url" is a bad instruction for someone who
has never opened a TOML file. ``devmind setup`` reads the ports, picks one, and can
write the config for you - without starting anything or downloading anything.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .ports import DEFAULT_PORTS, PortReport, port_of, scan

OLLAMA_PORT = 11434
DOWNLOAD_HINT = "https://github.com/ggml-org/llama.cpp/releases"


class SetupError(RuntimeError):
    """No usable plan exists (for example, every port in the range is taken)."""


@dataclass
class SetupPlan:
    kind: str
    port: int
    base_url: str
    start_command: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "port": self.port,
            "base_url": self.base_url,
            "start_command": self.start_command,
            "notes": list(self.notes),
        }


def find_gguf(dirs: list[Path] | None = None) -> Path | None:
    """The first ``*.gguf`` in the usual download folders, if there is exactly one."""
    roots = dirs or [
        Path.home() / "models",
        Path.home() / ".cache" / "llama.cpp",
        Path.home() / "Downloads",
    ]
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            found.extend(sorted(path for path in root.glob("*.gguf") if path.is_file()))
        except OSError:
            continue
    return found[0] if found else None


def build_plan(
    config,
    *,
    kind: str | None = None,
    port: int | None = None,
    gguf: str | Path | None = None,
    reports: list[PortReport] | None = None,
    has_llama_server: bool | None = None,
    has_ollama: bool | None = None,
) -> SetupPlan:
    reports = reports if reports is not None else scan(DEFAULT_PORTS)
    by_port = {report.port: report for report in reports}
    notes: list[str] = []

    if has_llama_server is None:
        has_llama_server = shutil.which("llama-server") is not None
    if has_ollama is None:
        has_ollama = shutil.which("ollama") is not None

    if kind == "ollama":
        ollama = by_port.get(OLLAMA_PORT)
        if ollama is not None and ollama.usable:
            notes.append(f"Ollama is answering on 11434: {ollama.detail}")
        else:
            notes.append(
                "Ollama is not answering on 11434 yet. Install it, start it (it sits in the "
                "tray), then run `ollama pull qwen2.5-coder:7b`."
            )
        notes.append("keep Ollama running in the background while you use DevMind")
        return SetupPlan(
            kind="ollama",
            port=OLLAMA_PORT,
            base_url=config.ollama.base_url,
            start_command="ollama serve",
            notes=notes,
        )

    usable = next((report for report in reports if report.usable), None)
    configured_port = port_of(config.llamacpp.base_url)

    if port is not None:
        target = port
    elif usable is not None:
        target = usable.port
        notes.append(f"found {usable.kind} on port {target}: {usable.detail}")
    elif configured_port in by_port and by_port[configured_port].free:
        target = configured_port
        notes.append(f"port {target} is free")
    else:
        free = next((report.port for report in reports if report.free), None)
        if free is None:
            raise SetupError(
                "every port in the scan is taken; free one or pass --port with a port you know is free"
            )
        target = free
        if configured_port in by_port:
            other = by_port[configured_port]
            notes.append(
                f"port {configured_port} is used by something else ({other.detail}) "
                "so that address can never answer"
            )

    model = str(gguf) if gguf else "<your-model.gguf>"
    command = f"llama-server -m {model} --port {target} -c 16384 -ngl 99"
    if not has_llama_server:
        notes.append(f"llama-server is not on PATH; download it from {DOWNLOAD_HINT}")
    if has_ollama:
        notes.append(
            "Ollama is installed: `ollama pull qwen2.5-coder:7b`, then "
            "`devmind setup --kind ollama --write`"
        )
    return SetupPlan(
        kind="llamacpp",
        port=target,
        base_url=f"http://127.0.0.1:{target}",
        start_command=command,
        notes=notes,
    )


def apply_plan(config, plan: SetupPlan) -> bool:
    """Write the plan into the config. Returns True when something actually changed."""
    changed = config.backend.kind != plan.kind
    config.backend.kind = plan.kind
    if plan.kind == "llamacpp" and config.llamacpp.base_url.rstrip("/") != plan.base_url:
        config.llamacpp.base_url = plan.base_url
        changed = True
    if changed:
        config.save()
    return changed
