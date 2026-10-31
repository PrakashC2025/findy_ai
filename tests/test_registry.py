from __future__ import annotations

from pathlib import Path

from devmind.registry import BUNDLED_REGISTRY, Registry


def test_bundled_registry_is_complete() -> None:
    registry = Registry.load()
    assert len(registry.models) >= 15
    assert registry.by_role("fast-small")
    assert registry.by_role("strong-coder")
    assert registry.by_role("embed")
    for model in registry.models:
        assert model.size_gb > 0
        assert 0 <= model.quality <= 100
        assert model.id
        assert model.roles


def test_best_respects_memory_budget(fake_hw) -> None:
    registry = Registry.load()
    best = registry.best("strong-coder", fake_hw)
    assert best is not None
    assert best.fits(fake_hw)
    # A 20 GB model cannot be the pick on a 9.3 GB budget.
    assert best.size_gb <= fake_hw.usable_memory_gb


def test_best_can_require_installed(fake_hw) -> None:
    registry = Registry.load()
    best = registry.best("strong-coder", fake_hw, installed={"qwen2.5-coder:7b"})
    assert best is not None and best.id == "qwen2.5-coder:7b"
    assert registry.best("strong-coder", fake_hw, installed={"nope"}) is None


def test_resolve_is_fuzzy_but_safe() -> None:
    registry = Registry.load()
    assert registry.resolve("qwen2.5-coder:7b").id == "qwen2.5-coder:7b"
    assert registry.resolve("qwen2.5-coder").id.startswith("qwen2.5-coder")
    assert registry.resolve("does-not-exist") is None


def test_user_override_merges_by_id(tmp_path: Path) -> None:
    override = tmp_path / "models.toml"
    override.write_text(
        "[[models]]\n"
        'id = "qwen2.5-coder:7b"\n'
        'family = "Qwen2.5-Coder"\n'
        "params_b = 7.0\n"
        "size_gb = 4.7\n"
        'roles = ["strong-coder"]\n'
        "quality = 99\n"
        "\n"
        "[[models]]\n"
        'id = "my-local:1b"\n'
        'family = "My Local"\n'
        "params_b = 1.0\n"
        "size_gb = 0.8\n"
        'roles = ["fast-small"]\n'
    )
    registry = Registry.load(override)
    assert BUNDLED_REGISTRY.exists()
    assert str(override) in registry.source_files
    assert registry.get("qwen2.5-coder:7b").quality == 99
    assert registry.get("my-local:1b").has_role("fast-small")
    assert len([m for m in registry.models if m.id == "qwen2.5-coder:7b"]) == 1
