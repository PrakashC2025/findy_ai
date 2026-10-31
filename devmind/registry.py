"""Model registry: what DevMind is allowed to run, and what each model is for.

The registry is data, not code: ``devmind/models.toml`` ships with the package
and ``~/.devmind/models.toml`` overrides it.  Adding a model never requires
touching Python.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from collections.abc import Iterable

from .hardware import Hardware

BUNDLED_REGISTRY = Path(__file__).with_name("models.toml")

ROLES = ("fast-small", "strong-coder", "reasoning", "judge", "draft", "embed")


@dataclass
class Model:
    id: str
    family: str
    params_b: float
    size_gb: float
    roles: tuple[str, ...]
    quality: float = 50.0
    context: int = 8192
    quant: str = "q4_K_M"
    license: str = "unknown"
    training_ok: bool = False
    strengths: tuple[str, ...] = ()
    notes: str = ""
    cloud: bool = False

    # ------------------------------------------------------------------ checks
    def fits(self, hw: Hardware) -> bool:
        return hw.fits(self.size_gb)

    def estimated_tok_s(self, hw: Hardware) -> float:
        return hw.estimate_tokens_per_second(self.size_gb)

    def usable(self, hw: Hardware, min_tok_s: float = 6.0) -> bool:
        """Fits in memory *and* fast enough to feel interactive."""
        return self.fits(hw) and self.estimated_tok_s(hw) >= min_tok_s

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def to_dict(self, hw: Hardware | None = None) -> dict:
        data = asdict(self)
        data["roles"] = list(self.roles)
        data["strengths"] = list(self.strengths)
        if hw is not None:
            data["fits"] = self.fits(hw)
            data["tok_s"] = self.estimated_tok_s(hw)
            data["usable"] = self.usable(hw)
        return data


@dataclass
class Registry:
    models: list[Model] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------- load
    @classmethod
    def load(cls, override: Path | None = None) -> Registry:
        reg = cls()
        reg._merge_file(BUNDLED_REGISTRY)
        if override and override.exists():
            reg._merge_file(override)
        return reg

    def _merge_file(self, path: Path) -> None:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise SystemExit(f"Cannot read model registry {path}: {exc}") from exc
        by_id = {model.id: model for model in self.models}
        for raw in data.get("models", []):
            model = self._parse(raw, path)
            by_id[model.id] = model
        self.models = list(by_id.values())
        self.source_files.append(str(path))

    @staticmethod
    def _parse(raw: dict, path: Path) -> Model:
        try:
            model_id = str(raw["id"]).strip()
            family = str(raw.get("family", model_id.split(":")[0]))
            params_b = float(raw.get("params_b", 7.0))
            size_gb = float(raw.get("size_gb") or params_b * 0.62 + 0.4)
            roles = tuple(role for role in raw.get("roles", []) if role in ROLES)
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"Bad model entry in {path}: {exc}") from exc
        if not roles:
            roles = ("strong-coder",)
        return Model(
            id=model_id,
            family=family,
            params_b=params_b,
            size_gb=size_gb,
            roles=roles,
            quality=float(raw.get("quality", 50)),
            context=int(raw.get("context", 8192)),
            quant=str(raw.get("quant", "q4_K_M")),
            license=str(raw.get("license", "unknown")),
            training_ok=bool(raw.get("training_ok", False)),
            strengths=tuple(raw.get("strengths", [])),
            notes=str(raw.get("notes", "")),
        )

    def upsert(self, model: Model) -> None:
        """Add or replace a registry entry (used for the llama.cpp model)."""
        self.models = [existing for existing in self.models if existing.id != model.id]
        self.models.append(model)

    # ---------------------------------------------------------------- queries
    def all(self) -> list[Model]:
        return list(self.models)

    def get(self, model_id: str) -> Model | None:
        for model in self.models:
            if model.id == model_id:
                return model
        return None

    def by_role(self, role: str) -> list[Model]:
        return [m for m in self.models if m.has_role(role)]

    def by_roles(self, roles: Iterable[str]) -> list[Model]:
        wanted = set(roles)
        return [m for m in self.models if wanted & set(m.roles)]

    def embedding_models(self) -> list[Model]:
        return self.by_role("embed")

    def resolve(self, name: str) -> Model | None:
        """Accept ``qwen2.5-coder`` and return the best matching quantisation."""
        if not name:
            return None
        exact = self.get(name)
        if exact:
            return exact
        lowered = name.lower()
        matches = [m for m in self.models if lowered in m.id.lower() or lowered in m.family.lower()]
        if not matches:
            return None
        return max(matches, key=lambda m: m.quality)

    def best(
        self,
        role: str,
        hw: Hardware,
        *,
        min_quality: float = 0.0,
        min_tok_s: float = 6.0,
        require_fit: bool = True,
        installed: set[str] | None = None,
    ) -> Model | None:
        pool = [
            m
            for m in self.by_role(role)
            if m.quality >= min_quality and (not require_fit or m.usable(hw, min_tok_s))
        ]
        if installed is not None:
            pool = [m for m in pool if m.id in installed]
        if not pool:
            return None
        return max(pool, key=lambda m: (m.quality, -m.size_gb))

    def smallest_for_role(self, role: str) -> Model | None:
        pool = self.by_role(role)
        return min(pool, key=lambda m: m.size_gb) if pool else None
