"""Deterministic backend for tests, CI and `--demo`.

It never pretends to be a model: every answer says it is demo mode and shows
exactly what DevMind would have sent to a real backend.
"""

from __future__ import annotations

import asyncio
from typing import Any
from collections.abc import AsyncIterator

from .base import Health, Message, chunks


class DemoBackend:
    name = "demo"

    async def list_models(self) -> list[str]:
        return ["demo"]

    async def health(self) -> Health:
        return Health(True, "demo backend - no model is loaded", models=0)

    async def chat(
        self,
        messages: list[Message],
        model: str = "demo",
        *,
        options: dict[str, Any] | None = None,
        keep_alive: str | None = None,
    ) -> AsyncIterator[str]:
        prompt = next(
            (message["content"] for message in reversed(messages) if message["role"] == "user"),
            "",
        )
        text = (
            "**Demo mode - no inference backend is running.**\n\n"
            "DevMind routed your request, built the prompt and would now stream an "
            "answer from your own machine. Nothing is fabricated here on purpose.\n\n"
            f"- Prompt received: `{prompt.strip()[:180]}`\n"
            f"- Model requested: `{model}`\n\n"
            "Pick one backend:\n"
            "1. **Home PC (best quality):** run `llama-server -m <model.gguf> "
            '--port 8080` and set `[backend] kind = "llamacpp"`.\n'
            "2. **Desktop, simplest:** install Ollama from https://ollama.com/download, "
            "then `devmind pull qwen2.5-coder:7b`.\n"
            "3. **Phone only:** use the PWA, which runs a 0.5B-3B model on-device.\n"
        )
        for piece in chunks(text, 18):
            yield piece
            await asyncio.sleep(0.01)

    async def aclose(self) -> None:
        return None
