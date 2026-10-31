"""Ollama backend (optional convenience for desktop users).

Ollama is not required — ``llamacpp`` is the default backend — but it remains
the quickest way to try models on a laptop, so DevMind keeps first-class
support for it.
"""

from __future__ import annotations

import json
from typing import Any
from collections.abc import AsyncIterator

import httpx

from .base import BackendUnavailable, Health, LoopClient, Message


class OllamaBackend:
    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        *,
        connect_timeout: float = 3.0,
        request_timeout: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._http = LoopClient(
            base_url,
            connect_timeout=connect_timeout,
            request_timeout=request_timeout,
            transport=transport,
        )

    @property
    def base_url(self) -> str:
        return self._http.base_url

    # ------------------------------------------------------------------ status
    async def version(self) -> str:
        try:
            response = await self._http.client.get("/api/version")
            response.raise_for_status()
            return str(response.json().get("version", "unknown"))
        except (httpx.HTTPError, json.JSONDecodeError):
            return ""

    async def list_models(self) -> list[str]:
        try:
            response = await self._http.client.get("/api/tags")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise BackendUnavailable(self.explain(exc)) from exc
        payload = response.json()
        return sorted(
            entry.get("name", "") for entry in payload.get("models", []) if entry.get("name")
        )

    async def health(self) -> Health:
        try:
            names = await self.list_models()
        except BackendUnavailable as exc:
            return Health(False, str(exc))
        version = await self.version()
        return Health(
            True,
            f"Ollama at {self._http.base_url}",
            version=version,
            models=len(names),
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
            async with self._http.client.stream("POST", "/api/chat", json=payload) as response:
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
            raise BackendUnavailable(self.explain(exc)) from exc
        except httpx.ReadTimeout as exc:
            raise BackendUnavailable(
                f"Ollama timed out after {self._http.request_timeout}s. "
                "Try a smaller model or reduce num_ctx in ~/.devmind/config.toml."
            ) from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailable(self.explain(exc)) from exc

    # -------------------------------------------------------------------- pull
    async def pull(self, model: str) -> AsyncIterator[dict[str, Any]]:
        """Stream download progress: {"status": str, "completed": int, "total": int}."""
        try:
            async with self._http.client.stream(
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
            raise BackendUnavailable(self.explain(exc)) from exc

    # ------------------------------------------------------------------- close
    async def aclose(self) -> None:
        await self._http.aclose()

    def explain(self, exc: Exception) -> str:
        return (
            f"Cannot reach Ollama at {self._http.base_url}. Install it from "
            "https://ollama.com/download, then run `ollama serve` "
            "(or `brew services start ollama` on macOS).\n"
            f"Underlying error: {type(exc).__name__}: {exc}"
        )
