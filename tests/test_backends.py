from __future__ import annotations

import asyncio

import pytest

from devmind.backends import BackendUnavailable, DemoBackend, OllamaBackend


def test_demo_backend_is_honest_and_streams() -> None:
    async def run() -> list[str]:
        backend = DemoBackend()
        chunks = [
            chunk
            async for chunk in backend.chat(
                [{"role": "user", "content": "write quicksort"}], "demo"
            )
        ]
        await backend.aclose()
        return chunks

    chunks = asyncio.run(run())
    text = "".join(chunks)
    assert len(chunks) > 3
    assert "Demo mode" in text
    assert "write quicksort" in text
    assert "ollama.com/download" in text


def test_unreachable_ollama_raises_actionable_error() -> None:
    backend = OllamaBackend("http://127.0.0.1:9", connect_timeout=0.2, request_timeout=1.0)

    async def run() -> None:
        await backend.list_models()

    with pytest.raises(BackendUnavailable) as excinfo:
        asyncio.run(run())
    message = str(excinfo.value)
    assert "Cannot reach Ollama" in message
    assert "ollama serve" in message


def test_client_rebinds_across_event_loops() -> None:
    """The CLI calls asyncio.run() per turn; that must not break the client."""
    backend = OllamaBackend("http://127.0.0.1:9", connect_timeout=0.2, request_timeout=1.0)

    async def attempt() -> None:
        with pytest.raises(BackendUnavailable):
            await backend.list_models()

    asyncio.run(attempt())
    asyncio.run(attempt())  # second loop, same backend instance
