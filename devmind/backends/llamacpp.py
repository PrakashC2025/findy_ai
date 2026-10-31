"""llama.cpp server backend (OpenAI-compatible HTTP).

Runs on the user's own computer or home PC; the phone connects over a private
tunnel (Tailscale, WireGuard, SSH) — no third-party inference service involved.

Start the server with, for example::

    llama-server -m qwen2.5-coder-7b-instruct-q4_k_m.gguf \\
        --host 127.0.0.1 --port 8080 -c 16384 -ngl 99

Then point DevMind at it::

    [backend]
    kind = "llamacpp"

    [llamacpp]
    base_url = "http://127.0.0.1:8080"
    model = "qwen2.5-coder-7b"    # your label; the server serves one model
"""

from __future__ import annotations

import json
from typing import Any
from collections.abc import AsyncIterator

import httpx

from .base import BackendUnavailable, Health, LoopClient, Message


class LlamaCppBackend:
    name = "llamacpp"
    label = "llama.cpp server"
    single_model = True  # a llama.cpp server serves exactly one GGUF

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        *,
        model: str = "local-model",
        api_key: str = "",
        connect_timeout: float = 3.0,
        request_timeout: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.model = model
        self._http = LoopClient(
            base_url,
            headers=headers,
            connect_timeout=connect_timeout,
            request_timeout=request_timeout,
            transport=transport,
        )

    # ------------------------------------------------------------------ status
    async def list_models(self) -> list[str]:
        try:
            response = await self._http.client.get("/v1/models")
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise BackendUnavailable(self.explain(exc)) from exc
        ids = [
            entry.get("id", "")
            for entry in payload.get("data", [])
            if isinstance(entry, dict) and entry.get("id")
        ]
        return ids or [self.model]

    async def health(self) -> Health:
        try:
            response = await self._http.client.get("/health")
            if response.status_code >= 500:
                return Health(
                    False,
                    f"server at {self._http.base_url} is not ready (HTTP {response.status_code})",
                )
            payload: dict[str, Any] = {}
            if response.content:
                try:
                    payload = response.json()
                except json.JSONDecodeError:
                    payload = {}
            status = str(payload.get("status", "ok")).lower()
            if status not in {"ok", "no slot available"}:
                return Health(False, f"server reported status '{status}'")
            models = await self.list_models()
            return Health(
                True,
                f"llama.cpp server at {self._http.base_url}",
                version=str(payload.get("build", "")),
                models=len(models),
            )
        except BackendUnavailable as exc:
            return Health(False, str(exc))
        except httpx.HTTPError as exc:
            return Health(False, self.explain(exc))

    # -------------------------------------------------------------------- chat
    async def chat(
        self,
        messages: list[Message],
        model: str,
        *,
        options: dict[str, Any] | None = None,
        keep_alive: str | None = None,
    ) -> AsyncIterator[str]:
        # llama.cpp keeps the model loaded, so keep_alive is irrelevant here.
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
        }
        if options:
            payload.update(_map_options(options))

        try:
            async with self._http.client.stream(
                "POST", "/v1/chat/completions", json=payload
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "ignore")[:400]
                    raise BackendUnavailable(
                        f"llama.cpp returned {response.status_code} for model '{payload['model']}': "
                        f"{body}"
                    )
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if event.get("error"):
                        raise BackendUnavailable(str(event["error"]))
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content") or ""
                    if piece:
                        yield piece
        except httpx.ConnectError as exc:
            raise BackendUnavailable(self.explain(exc)) from exc
        except httpx.ReadTimeout as exc:
            raise BackendUnavailable(
                f"{self.label} did not answer within {self._http.request_timeout}s. "
                "Try a shorter prompt, a smaller model, or a lower -c (context)."
            ) from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailable(self.explain(exc)) from exc

    # ------------------------------------------------------------------- close
    async def aclose(self) -> None:
        await self._http.aclose()

    def explain(self, exc: Exception) -> str:
        return (
            f"Cannot reach the llama.cpp server at {self._http.base_url}. "
            "Start it on the machine that owns the model:\n"
            "  llama-server -m <model.gguf> --host 127.0.0.1 --port 8080 -c 16384 -ngl 99\n"
            "If it runs on your home PC, connect through your private tunnel "
            "(e.g. Tailscale) and put that address in [llamacpp] base_url.\n"
            f"Underlying error: {type(exc).__name__}: {exc}"
        )


def _map_options(options: dict[str, Any]) -> dict[str, Any]:
    """Translate DevMind's Ollama-style options to OpenAI-style parameters."""
    mapping = {
        "temperature": "temperature",
        "top_p": "top_p",
        "seed": "seed",
        "num_predict": "max_tokens",
    }
    mapped = {
        target: options[source]
        for source, target in mapping.items()
        if source in options and options[source] is not None
    }
    # num_ctx is a server flag in llama.cpp (-c), not a request parameter.
    return mapped
