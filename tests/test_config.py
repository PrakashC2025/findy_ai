from __future__ import annotations

from pathlib import Path

from devmind.config import Config


def test_defaults_are_local_and_conservative(tmp_home: Path) -> None:
    config = Config.load(tmp_home)
    assert config.ollama.base_url.startswith("http://127.0.0.1")
    assert config.models.temperature <= 0.3  # correctness over creativity
    assert config.privacy.redact_secrets is True
    assert config.offline is False
    assert config.db_path.parent == tmp_home


def test_roundtrip(tmp_home: Path) -> None:
    config = Config.load(tmp_home)
    config.models.temperature = 0.42
    config.models.num_ctx = 16384
    config.ollama.base_url = "http://localhost:11500"
    config.router.prefer_speed = True
    config.save()
    assert config.config_path.exists()

    again = Config.load(tmp_home)
    assert again.offline is False
    assert again.models.temperature == 0.42
    assert again.models.num_ctx == 16384
    assert again.ollama.base_url == "http://localhost:11500"
    assert again.router.prefer_speed is True


def test_offline_flag_survives_a_roundtrip(tmp_home: Path) -> None:
    config = Config.load(tmp_home)
    config.offline = True
    config.save()
    assert Config.load(tmp_home).offline is True


def test_env_overrides_file(tmp_home: Path, monkeypatch) -> None:
    config = Config.load(tmp_home)
    config.ollama.base_url = "http://127.0.0.1:11434"
    config.save()

    monkeypatch.setenv("DEVMIND_OLLAMA_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("DEVMIND_MODEL", "qwen2.5-coder:7b")
    monkeypatch.setenv("DEVMIND_OFFLINE", "1")
    monkeypatch.setenv("DEVMIND_NUM_CTX", "4096")

    again = Config.load(tmp_home)
    assert again.ollama.base_url == "http://127.0.0.1:9999"
    assert again.models.default == "qwen2.5-coder:7b"
    assert again.offline is True
    assert again.models.num_ctx == 4096


def test_unknown_section_is_ignored_not_fatal(tmp_home: Path) -> None:
    tmp_home.mkdir(parents=True, exist_ok=True)
    (tmp_home / "config.toml").write_text("[models]\ntemperature = 0.2\n[future]\nthing = 1\n")
    config = Config.load(tmp_home)
    assert config.models.temperature == 0.2
