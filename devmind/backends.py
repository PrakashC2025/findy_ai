"""Inference backends.

Only two exist, on purpose:

* :class:`OllamaBackend` — talks to a local Ollama server over loopback HTTP.
* :class:`DemoBackend`   — deterministic stand-in used by tests and by
  ``--demo`` when no model is installed.  It is clearly labelled in every
  answer and never pretends to be a model.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol
from collections.abc import AsyncIterator

import httpx

Message = dict[str, str]


class BackendUnavailable(RuntimeError):
    """Raised when the local inference server cannot be reached."""


class Backend(Protocol):
    name: str

    async def list_models(self) -> list[str]: ...

    async def chat(
        self,
        messages: list[Message],
        model: str,
        *,
        options: dict[str, Any] | None = None,
        keep_alive: str | None = None,
    ) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...


class OllamaBackend:
    """Minimal Ollama client: chat streaming, model listing, pulls."""

    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        *,
        connect_timeout: float = 3.0,
        request_timeout: float = 300.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # The CLI calls asyncio.run() per command (and per chat turn).  An httpx
    # AsyncClient is bound to the loop that created it, so we bind lazily and
    # rebuild when the loop changes.  This also keeps `import devmind` free of
    # side effects.
    @property
    def client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._client.is_closed or self._loop is not loop:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.request_timeout, connect=self.connect_timeout),
                headers={"User-Agent": "devmind/0.1"},
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            )
            self._loop = loop
        return self._client

    # ------------------------------------------------------------------ status
    async def version(self) -> str:
        try:
            response = await self.client.get("/api/version")
            response.raise_for_status()
            return str(response.json().get("version", "unknown"))
        except httpx.HTTPError:
            return ""

    async def list_models(self) -> list[str]:
        try:
            response = await self.client.get("/api/tags")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise BackendUnavailable(self._explain(exc)) from exc
        payload = response.json()
        return sorted(
            entry.get("name", "") for entry in payload.get("models", []) if entry.get("name")
        )

    # -------------------------------------------------------------------- chat
    async def chat(
        self,
        messages: list[Message],
        model: str,
        *,
        options: dict[str, Any] | None = None,
        keep_alive: str | None = "30m",
    ) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": options or {},
        }
        if keep_alive:
            payload["keep_alive"] = keep_alive
        try:
            async with self.client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "ignore")[:400]
                    raise BackendUnavailable(
                        f"Ollama returned {response.status_code} for model '{model}': {body}"
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise BackendUnavailable(str(chunk["error"]))
                    piece = (chunk.get("message") or {}).get("content", "")
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        break
        except httpx.ConnectError as exc:
            raise BackendUnavailable(self._explain(exc)) from exc
        except httpx.ReadTimeout as exc:
            raise BackendUnavailable(
                f"Ollama timed out after {self.client.timeout.read}s. "
                "Try a smaller model or reduce num_ctx in ~/.devmind/config.toml."
            ) from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailable(self._explain(exc)) from exc

    # -------------------------------------------------------------------- pull
    async def pull(self, model: str) -> AsyncIterator[dict[str, Any]]:
        """Stream download progress: {"status": str, "completed": int, "total": int}."""
        try:
            async with self.client.stream(
                "POST",
                "/api/pull",
                json={"model": model, "stream": True},
                timeout=httpx.Timeout(None, connect=10.0),
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "ignore")[:400]
                    raise BackendUnavailable(f"Pull failed ({response.status_code}): {body}")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except httpx.ConnectError as exc:
            raise BackendUnavailable(self._explain(exc)) from exc

    # ------------------------------------------------------------------- close
    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def _explain(self, exc: Exception) -> str:
        return (
            f"Cannot reach Ollama at {self.base_url}. Install it from "
            "https://ollama.com/download, then run `ollama serve` "
            "(or `brew services start ollama` on macOS). "
            f"Underlying error: {type(exc).__name__}: {exc}"
        )


class DemoBackend:
    """Deterministic, clearly-labelled stand-in when no model is available."""

    name = "demo"

    async def list_models(self) -> list[str]:
        return ["demo"]

    async def chat(
        self,
        messages: list[Message],
        model: str = "demo",
        *,
        options: dict[str, Any] | None = None,
        keep_alive: str | None = None,
    ) -> AsyncIterator[str]:
        prompt = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        text = (
            "**Demo mode — no local model is running.**\n\n"
            "DevMind routed your request, built the prompt and would now stream an "
            "answer from Ollama. Nothing is fabricated here on purpose.\n\n"
            f"- Prompt received: `{prompt.strip()[:180]}`\n"
            f"- Model requested: `{model}`\n\n"
            "To get real answers:\n"
            "1. Install Ollama: https://ollama.com/download\n"
            "2. `devmind pull qwen2.5-coder:7b` (or the model `devmind models` recommends)\n"
            "3. Run your command again.\n"
        )
        for chunk in _chunks(text, 18):
            yield chunk
            await asyncio.sleep(0.01)

    async def aclose(self) -> None:
        return None


def _chunks(text: str, size: int) -> list[str]:
    words = text.split(" ")
    out: list[str] = []
    current = ""
    for word in words:
        current += word + " "
        if len(current) >= size:
            out.append(current)
            current = ""
    if current:
        out.append(current)
    return out
