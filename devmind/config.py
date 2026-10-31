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
class BackendConfig:
    """Which inference backend the home PC uses.

    ``llamacpp`` (default) talks to a llama.cpp server on the user's own
    machine or home PC.  ``ollama`` is kept because it is the easiest way to run
    models on a desktop.  ``demo`` answers with a labelled placeholder and is
    used by tests.  The on-device (WebLLM/wllama) backend lives in the PWA and
    speaks the same interface from the browser side.
    """

    kind: str = "llamacpp"  # llamacpp | ollama | demo


@dataclass
class LlamaCppConfig:
    """A user-owned llama.cpp server (usually reached over a private tunnel)."""

    base_url: str = "http://127.0.0.1:8080"
    model: str = "local-model"
    api_key: str = ""  # only if the tunnel adds its own auth
    model_quality: float = 75.0  # 0-100 prior used by the router
    model_size_gb: float = 0.0  # 0 = let the router assume it fits
    context: int = 16384
    connect_timeout_s: float = 3.0
    request_timeout_s: float = 300.0


@dataclass
class OllamaConfig:
    """Connection to a local Ollama server (never leaves the machine)."""

    base_url: str = "http://127.0.0.1:11434"
    connect_timeout_s: float = 3.0
    request_timeout_s: float = 300.0
    keep_alive: str = "30m"  # keep the model warm between requests


@dataclass
class RemoteConfig:
    """Opt-in OpenAI-compatible provider (user's own free tier).

    Off by default. Requires ``enabled`` + ``consent`` + an API key in the
    environment; the key is never written to the config file.
    """

    enabled: bool = False
    consent: bool = False
    provider: str = ""  # label only, e.g. "Groq free tier"
    base_url: str = ""  # e.g. https://api.groq.com/openai/v1
    api_key_env: str = "DEVMIND_REMOTE_KEY"
    models: list[str] = field(default_factory=list)
    model_quality: float = 85.0
    context: int = 32768
    connect_timeout_s: float = 10.0
    request_timeout_s: float = 180.0


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
    include_code: bool = True  # inject relevant chunks from the code index
    code_top_k: int = 4
    code_max_chars: int = 3200


@dataclass
class IndexConfig:
    """Code indexer limits: resumable, bounded, phone-friendly."""

    enabled: bool = True
    max_files: int = 20_000
    max_file_bytes: int = 512_000
    max_seconds: float = 120.0  # stop and resume next run
    batch_size: int = 200  # commit every N files
    window_lines: int = 60  # generic chunker window
    exclude: list[str] = field(
        default_factory=lambda: [
            ".git",
            ".hg",
            ".svn",
            "node_modules",
            ".venv",
            "venv",
            "dist",
            "build",
            "target",
            "vendor",
            "__pycache__",
            ".next",
            ".cache",
            "coverage",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".env",
            ".env.*",
            "*.pem",
            "*.key",
            "*.p12",
            "id_rsa*",
            "*credential*",
            "*secret*",
            "*.min.js",
            "*.map",
            "*.lock",
        ]
    )


@dataclass
class PipelineConfig:
    """Quality/speed trade-offs for the answer pipeline (step 4)."""

    cache: bool = True
    cache_threshold: float = 0.92  # cosine similarity for a cache hit
    max_candidates: int = 3  # self-consistency samples on hard tasks
    plan_hard_tasks: bool = True  # short plan before solving
    judge_tie_break: bool = True  # ask the judge only when scores tie
    min_confidence_to_cache: float = 0.35  # below this an answer is not reused


@dataclass
class GitHubConfig:
    """GitHub access. Login is your own account; there is no paid API key."""

    client_id: str = ""  # OAuth App client id for the device flow
    api_url: str = "https://api.github.com"
    scopes: str = "repo read:user"
    token_path: str = ""  # default: $DEVMIND_HOME/credentials.enc


@dataclass
class PrivacyConfig:
    """Privacy guards.  Telemetry does not exist in DevMind — by construction."""

    redact_secrets: bool = True  # scrub API keys/tokens before anything is stored


@dataclass
class Config:
    home: Path = field(default_factory=default_home)
    backend: BackendConfig = field(default_factory=BackendConfig)
    llamacpp: LlamaCppConfig = field(default_factory=LlamaCppConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    remote: RemoteConfig = field(default_factory=RemoteConfig)
    models: ModelConfig = field(default_factory=ModelConfig)
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    router: RouterConfig = field(default_factory=RouterConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    index: IndexConfig = field(default_factory=IndexConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
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

    @property
    def credentials_path(self) -> Path:
        return (
            Path(self.github.token_path).expanduser()
            if self.github.token_path
            else self.home / "credentials.enc"
        )

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
        if env.get("DEVMIND_BACKEND"):
            self.backend.kind = env["DEVMIND_BACKEND"]
        if env.get("DEVMIND_LLAMACPP_URL"):
            self.llamacpp.base_url = env["DEVMIND_LLAMACPP_URL"]
        if env.get("DEVMIND_LLAMACPP_MODEL"):
            self.llamacpp.model = env["DEVMIND_LLAMACPP_MODEL"]
        if env.get("DEVMIND_LLAMACPP_KEY"):
            self.llamacpp.api_key = env["DEVMIND_LLAMACPP_KEY"]
        if env.get("DEVMIND_GITHUB_CLIENT_ID"):
            self.github.client_id = env["DEVMIND_GITHUB_CLIENT_ID"]
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
