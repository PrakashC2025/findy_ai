"""Opt-in remote provider: consent, offline, keys, and registry entries."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from devmind.backends import BackendUnavailable, RemoteBackend, get_backend
from devmind.backends.remote import register_remote_models
from devmind.config import Config
from devmind.registry import Registry


def _config(tmp_path: Path, **remote) -> Config:
    config = Config.load(tmp_path / "home")
    config.backend.kind = "remote"
    for key, value in remote.items():
        setattr(config.remote, key, value)
    return config


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": [{"id": "free-model"}]})
    if request.url.path.endswith("/chat/completions"):
        body = 'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    return httpx.Response(404)


def _run(coro):
    return asyncio.run(coro)


def test_disabled_provider_reports_why(tmp_path: Path) -> None:
    config = _config(tmp_path)
    backend = RemoteBackend.from_config(config)
    health = _run(backend.health())
    assert health.ok is False
    assert "disabled" in health.detail
    with pytest.raises(BackendUnavailable, match="disabled"):
        _run(_collect(backend))


def test_consent_is_required_even_when_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEVMIND_REMOTE_KEY", "test-key")
    config = _config(tmp_path, enabled=True, consent=False, base_url="http://test", models=["m"])
    health = _run(RemoteBackend.from_config(config).health())
    assert health.ok is False
    assert "consent" in health.detail


def test_offline_mode_wins(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEVMIND_REMOTE_KEY", "test-key")
    config = _config(tmp_path, enabled=True, consent=True, base_url="http://test", models=["m"])
    config.offline = True
    health = _run(RemoteBackend.from_config(config).health())
    assert health.ok is False
    assert "offline" in health.detail


def test_missing_key_is_explained_without_leaking_anything(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DEVMIND_REMOTE_KEY", raising=False)
    config = _config(tmp_path, enabled=True, consent=True, base_url="http://test", models=["m"])
    health = _run(RemoteBackend.from_config(config).health())
    assert health.ok is False
    assert "DEVMIND_REMOTE_KEY" in health.detail
    assert "test-key" not in health.detail


def test_configured_provider_streams(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEVMIND_REMOTE_KEY", "test-key")
    config = _config(
        tmp_path,
        enabled=True,
        consent=True,
        base_url="http://test",
        provider="free tier",
        models=["free-model"],
    )
    backend = RemoteBackend.from_config(config, transport=httpx.MockTransport(_handler))
    health = _run(backend.health())
    assert health.ok is True
    assert "code leaves this device" in health.detail
    assert "hi" in _run(_collect(backend))


async def _collect(backend) -> str:
    pieces = []
    async for chunk in backend.chat(
        [{"role": "user", "content": "hello"}], "free-model", options={"temperature": 0.2}
    ):
        pieces.append(chunk)
    return "".join(pieces)


def test_factory_and_registry_integration(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEVMIND_REMOTE_KEY", "test-key")
    config = _config(
        tmp_path,
        enabled=True,
        consent=True,
        base_url="http://test",
        provider="free tier",
        models=["free-32b"],
    )
    backend = get_backend(config)
    assert isinstance(backend, RemoteBackend)
    assert backend.single_model is False

    registry = Registry.load(None)
    register_remote_models(registry, config)
    entry = registry.get("free-32b")
    assert entry is not None
    assert "strong-coder" in entry.roles
    assert json.dumps(entry.to_dict())  # serialisable for the /api/status payload


def test_registry_untouched_when_disabled(tmp_path: Path) -> None:
    config = _config(tmp_path, enabled=False, models=["free-32b"])
    registry = Registry.load(None)
    register_remote_models(registry, config)
    assert registry.get("free-32b") is None


def test_registry_skips_cloud_models_without_a_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DEVMIND_REMOTE_KEY", raising=False)
    config = _config(
        tmp_path, enabled=True, consent=True, base_url="http://test", models=["free-32b"]
    )
    registry = Registry.load(None)
    register_remote_models(registry, config)
    assert registry.get("free-32b") is None


def test_cloud_models_are_not_suggested_as_downloads(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEVMIND_REMOTE_KEY", "test-key")
    config = _config(
        tmp_path, enabled=True, consent=True, base_url="http://test", models=["free-32b"]
    )
    registry = Registry.load(None)
    register_remote_models(registry, config)
    entry = registry.get("free-32b")
    assert entry is not None and entry.cloud is True
    assert entry.to_dict()["cloud"] is True
