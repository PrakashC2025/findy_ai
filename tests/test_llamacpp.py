from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from devmind.backends import BackendUnavailable, LlamaCppBackend, get_backend
from devmind.config import Config


def run(coro):
    return asyncio.run(coro)


def test_health_and_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "build": "b4242"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen2.5-coder-7b"}]})
        return httpx.Response(404)

    backend = LlamaCppBackend(
        "http://test", model="qwen2.5-coder-7b", transport=httpx.MockTransport(handler)
    )

    async def scenario():
        health = await backend.health()
        models = await backend.list_models()
        await backend.aclose()
        return health, models

    health, models = run(scenario())
    assert health.ok is True
    assert "b4242" in health.version
    assert models == ["qwen2.5-coder-7b"]


def test_chat_streams_and_maps_options() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        body = (
            'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    backend = LlamaCppBackend("http://test", model="local", transport=httpx.MockTransport(handler))

    async def scenario() -> str:
        pieces = [
            piece
            async for piece in backend.chat(
                [{"role": "user", "content": "hi"}],
                "local",
                options={"temperature": 0.1, "num_predict": 64, "num_ctx": 8192, "seed": 7},
            )
        ]
        await backend.aclose()
        return "".join(pieces)

    assert run(scenario()) == "Hello world"
    payload = captured["payload"]
    assert payload["max_tokens"] == 64
    assert payload["temperature"] == 0.1
    assert "num_ctx" not in payload  # server flag, not a request parameter


def test_server_error_is_actionable() -> None:
    backend = LlamaCppBackend(
        "http://test",
        transport=httpx.MockTransport(lambda request: httpx.Response(500, text="boom")),
    )

    async def scenario():
        async for _ in backend.chat([{"role": "user", "content": "hi"}], "local"):
            pass

    with pytest.raises(BackendUnavailable) as excinfo:
        run(scenario())
    message = str(excinfo.value)
    assert "HTTP 500" in message
    assert "boom" in message


def test_html_404_says_another_server_is_on_the_port() -> None:
    html = (
        '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN">'
        "<html><head><title>404 Not Found</title></head>"
        "<body><h1>Not Found</h1><p>The requested URL was not found on this server.</p></body></html>"
    )
    backend = LlamaCppBackend(
        "http://localhost:8080",
        transport=httpx.MockTransport(lambda r: httpx.Response(404, text=html)),
    )

    async def scenario():
        async for _ in backend.chat([{"role": "user", "content": "hi"}], "local-model"):
            pass

    with pytest.raises(BackendUnavailable) as excinfo:
        run(scenario())
    message = str(excinfo.value)
    assert "not a llama.cpp server" in message
    assert "llama-server -m" in message
    assert "<html" not in message.lower()
    assert len(message) < 900


def test_health_reports_what_the_address_really_serves() -> None:
    html = "<html><head><title>404 Not Found</title></head><body></body></html>"
    backend = LlamaCppBackend(
        "http://localhost:8080",
        transport=httpx.MockTransport(lambda r: httpx.Response(404, text=html)),
    )

    health = run(backend.health())
    assert health.ok is False
    assert "not a llama.cpp server" in health.detail
    assert backend.base_url == "http://localhost:8080"


def test_unreachable_server_explains_how_to_start_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    backend = LlamaCppBackend("http://test", transport=httpx.MockTransport(handler))

    async def scenario():
        await backend.list_models()

    with pytest.raises(BackendUnavailable) as excinfo:
        run(scenario())
    message = str(excinfo.value)
    assert "llama-server" in message
    assert "Tailscale" in message


def test_factory_selects_backend(tmp_home) -> None:
    config = Config.load(tmp_home)
    assert config.backend.kind == "llamacpp"
    backend = get_backend(config)
    assert backend.name == "llamacpp"

    config.backend.kind = "ollama"
    assert get_backend(config).name == "ollama"

    config.backend.kind = "demo"
    assert get_backend(config).name == "demo"

    config.offline = True
    assert get_backend(config).name == "demo"


def test_health_falls_back_to_models_when_health_is_absent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json={"data": [{"id": "qwen2.5-coder-7b"}]})

    backend = LlamaCppBackend("http://test", transport=httpx.MockTransport(handler))
    health = run(backend.health())
    assert health.ok is True
    assert "OpenAI-compatible" in health.detail
    assert health.models == 1


def test_health_rejects_an_html_page_that_returns_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        html = "<html><head><title>Login</title></head><body>router</body></html>"
        return httpx.Response(200, text=html, headers={"server": "nginx"})

    backend = LlamaCppBackend("http://test", transport=httpx.MockTransport(handler))
    health = run(backend.health())
    assert health.ok is False
    assert "not a llama.cpp server" in health.detail
    assert "router" in health.detail.lower() or "login" in health.detail.lower()
    assert "nginx" in health.detail


def test_loopback_backends_never_use_a_proxy() -> None:
    local = LlamaCppBackend("http://127.0.0.1:8080")
    tunnelled = LlamaCppBackend("https://home-pc.example.ts.net")
    assert local.local is True
    assert local._http.trust_env is False
    assert tunnelled.local is False
    assert tunnelled._http.trust_env is True
