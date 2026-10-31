"""Typed configuration for DevMind.

Config lives in ``$DEVMIND_HOME/config.toml`` (default ``~/.devmind``) and is
plain TOML so users can read and edit it.  Environment variables override the
file for the handful of settings that matter in CI:

===========================  ==============================
``DEVMIND_HOME``             data directory
``DEVMIND_OLLAMA_URL``       Ollama base URL
``DEVMIND_MODEL``            force a default model
``DEVMIND_OFFLINE``          "1" disables every network call
===========================  ==============================
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


def default_home() -> Path:
    return Path(os.environ.get("DEVMIND_HOME", "~/.devmind")).expanduser()


@dataclass
class OllamaConfig:
    """Connection to a local Ollama server (never leaves the machine)."""

    base_url: str = "http://127.0.0.1:11434"
    connect_timeout_s: float = 3.0
    request_timeout_s: float = 300.0
    keep_alive: str = "30m"  # keep the model warm between requests


@dataclass
class ModelConfig:
    """Decoding defaults.  Low temperature by default: correctness over flair."""

    default: str = ""  # empty = let the router decide
    temperature: float = 0.15
    top_p: float = 0.9
    seed: int = 42
    num_ctx: int = 8192
    max_tokens: int = 2048
    system_extra: str = ""  # appended to the built-in system prompt


@dataclass
class HardwareConfig:
    """How aggressively to use the machine."""

    reserve_gb: float = 1.5  # memory never handed to the model
    min_tokens_per_s: float = 6.0  # below this, step down a model size
    allow_cpu_only: bool = True
    max_model_gb: float = 0.0  # 0 = derive from detected memory


@dataclass
class RouterConfig:
    """Routing policy."""

    prefer_speed: bool = False  # bias to fast-small even for hard tasks
    min_quality: float = 0.55  # don't pick models below this prior
    log_decisions: bool = True


@dataclass
class MemoryConfig:
    """Long-term memory behaviour."""

    enabled: bool = True
    top_k: int = 6
    min_score: float = 0.10  # below this a memory is noise, not context
    max_chars_in_prompt: int = 4000
    auto_capture_preferences: bool = True


@dataclass
class PrivacyConfig:
    """Privacy guards.  Telemetry does not exist in DevMind — by construction."""

    redact_secrets: bool = True  # scrub API keys/tokens before anything is stored


@dataclass
class Config:
    home: Path = field(default_factory=default_home)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    models: ModelConfig = field(default_factory=ModelConfig)
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    router: RouterConfig = field(default_factory=RouterConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    offline: bool = False

    # ------------------------------------------------------------------ paths
    @property
    def config_path(self) -> Path:
        return self.home / "config.toml"

    @property
    def db_path(self) -> Path:
        return self.home / "memory.db"

    @property
    def registry_override_path(self) -> Path:
        return self.home / "models.toml"

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache"

    def ensure_dirs(self) -> None:
        for path in (self.home, self.cache_dir):
            path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------- load
    @classmethod
    def load(cls, home: str | Path | None = None) -> Config:
        cfg = cls()
        if home:
            cfg.home = Path(home).expanduser()
        if cfg.config_path.exists():
            try:
                cfg.merge(tomllib.loads(cfg.config_path.read_text(encoding="utf-8")))
            except (OSError, tomllib.TOMLDecodeError) as exc:  # pragma: no cover
                raise SystemExit(f"Invalid config at {cfg.config_path}: {exc}") from exc
        cfg.apply_env()
        cfg.ensure_dirs()
        return cfg

    def merge(self, data: dict[str, Any]) -> None:
        for key, value in data.items():
            if key == "home" and isinstance(value, str):
                self.home = Path(value).expanduser()
                continue
            section = getattr(self, key, None)
            if section is None:
                continue
            if hasattr(section, "__dataclass_fields__") and isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if sub_key in {f.name for f in fields(section)}:
                        setattr(section, sub_key, sub_value)
            else:
                setattr(self, key, value)

    def apply_env(self) -> None:
        env = os.environ
        if env.get("DEVMIND_HOME"):
            self.home = Path(env["DEVMIND_HOME"]).expanduser()
        if env.get("DEVMIND_OLLAMA_URL"):
            self.ollama.base_url = env["DEVMIND_OLLAMA_URL"]
        if env.get("DEVMIND_MODEL"):
            self.models.default = env["DEVMIND_MODEL"]
        if env.get("DEVMIND_OFFLINE", "").lower() in {"1", "true", "yes"}:
            self.offline = True
        if env.get("DEVMIND_NUM_CTX", "").isdigit():
            self.models.num_ctx = int(env["DEVMIND_NUM_CTX"])

    # ------------------------------------------------------------------- save
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["home"] = str(self.home)
        return data

    def save(self) -> None:
        self.ensure_dirs()
        data = self.to_dict()
        # TOML scoping: every key after a [table] header belongs to that table,
        # so top-level scalars must be written *before* any section.
        top = [
            (key, value)
            for key, value in data.items()
            if key != "home" and not isinstance(value, dict)
        ]
        tables = [(key, value) for key, value in data.items() if isinstance(value, dict)]
        lines: list[str] = ["# DevMind configuration - edit freely, everything is local.", ""]
        lines += [f"{key} = {_toml(value)}" for key, value in top]
        lines.append("")
        for name, section in tables:
            lines.append(f"[{name}]")
            lines += [f"{sub_key} = {_toml(sub_value)}" for sub_key, sub_value in section.items()]
            lines.append("")
        tmp = self.config_path.with_suffix(".toml.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(self.config_path)


def _toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def load_config(home: str | Path | None = None) -> Config:
    """Convenience wrapper used by the CLI and the API."""
    return Config.load(home)
