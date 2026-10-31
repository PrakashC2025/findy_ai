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
import re
from typing import Any
from collections.abc import AsyncIterator

import httpx

from .base import BackendUnavailable, Health, LoopClient, Message, is_loopback


def looks_like_html(body: str) -> bool:
    """True for an HTML error page - the usual sign that *another* server is on the port."""
    head = (body or "").lstrip()[:400].lower()
    return head.startswith("<!doctype") or head.startswith("<html") or "<title>" in head


def page_title(body: str) -> str:
    """The <title> of an HTML page, trimmed - it often names the program answering."""
    match = re.search(r"<title[^>]*>(.*?)</title>", body or "", re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return " ".join(match.group(1).split())[:80]


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
        self.local = is_loopback(base_url)
        self._http = LoopClient(
            base_url,
            headers=headers,
            connect_timeout=connect_timeout,
            request_timeout=request_timeout,
            transport=transport,
            trust_env=not self.local,
        )

    @property
    def base_url(self) -> str:
        """The address this backend talks to, for status pages and error messages."""
        return str(self._http.base_url)

    # ------------------------------------------------------------------ status
    async def list_models(self) -> list[str]:
        try:
            response = await self._http.client.get("/v1/models")
            if response.status_code >= 400:
                raise BackendUnavailable(
                    self.http_problem(
                        response.status_code,
                        response.text,
                        what="GET /v1/models",
                        server=response.headers.get("server", ""),
                    )
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise BackendUnavailable(
                    self.http_problem(
                        response.status_code,
                        response.text,
                        what="GET /v1/models (did not return JSON)",
                        server=response.headers.get("server", ""),
                    )
                ) from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailable(self.explain(exc)) from exc
        ids = [
            entry.get("id", "")
            for entry in payload.get("data", [])
            if isinstance(entry, dict) and entry.get("id")
        ]
        return ids or [self.model]

    async def health(self) -> Health:
        """Probe /health, then /v1/models.

        Some OpenAI-compatible servers (LM Studio, vLLM, gateways) have no
        /health at all. What matters for chat is /v1/models, so a missing
        /health must not be reported as a broken server - but when both fail,
        the error shown is the one that explains the problem.
        """
        try:
            response = await self._http.client.get("/health")
        except httpx.HTTPError as exc:
            return Health(False, self.explain(exc))

        payload: dict[str, Any] = {}
        health_ok = False
        if response.status_code < 400 and not looks_like_html(response.text):
            if response.content:
                try:
                    payload = response.json()
                except json.JSONDecodeError:
                    payload = {}
            status = str(payload.get("status", "ok")).lower()
            if status in {"ok", "no slot available"}:
                health_ok = True
            else:
                return Health(False, f"the server reported status '{status}'")

        if health_ok:
            try:
                models = await self.list_models()
            except BackendUnavailable as exc:
                return Health(False, str(exc))
            return Health(
                True,
                f"llama.cpp server at {self._http.base_url}",
                version=str(payload.get("build", "")),
                models=len(models),
            )

        try:
            models = await self.list_models()
        except BackendUnavailable as exc:
            # One message, not two: /v1/models is the check that matters for chat.
            return Health(False, str(exc))
        return Health(
            True,
            f"OpenAI-compatible server at {self._http.base_url} (no /health endpoint)",
            models=len(models),
        )

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
                    body = (await response.aread()).decode("utf-8", "ignore")[:600]
                    raise BackendUnavailable(
                        self.http_problem(
                            response.status_code,
                            body,
                            what=f"POST /v1/chat/completions (model '{payload['model']}')",
                        )
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

    # ---------------------------------------------------------------- problems
    def http_problem(self, status: int, body: str, *, what: str = "", server: str = "") -> str:
        """Turn an HTTP error into something a person can act on.

        The important case is a 404 that is actually an HTML page: usually another
        program (a Flask app, a router admin page, the wrong port) is answering
        instead of llama.cpp. Printing 300 characters of its HTML helps nobody.
        """
        base = self._http.base_url
        where = f"{base} answered {what or 'the request'}"
        if looks_like_html(body):
            clues = []
            title = page_title(body)
            if title:
                clues.append(f"page title: {title!r}")
            if server:
                from ..ports import server_note

                clue = f"Server header: {server}"
                note = server_note(server)
                if note:
                    clue += f" - {note}"
                clues.append(clue)
            clue_text = f" ({'; '.join(clues)})" if clues else ""
            proxy_hint = ""
            if self.local:
                proxy_hint = (
                    "\n  * if this machine sets HTTP_PROXY/ALL_PROXY, a proxy can answer "
                    "loopback requests; DevMind ignores proxies for 127.0.0.1, so check "
                    "that something is really listening there (devmind ports)"
                )
            return (
                f"{where} with HTTP {status} and an HTML page{clue_text}, so something "
                f"else is listening on that port - it is not a llama.cpp server.\n"
                "Fix one of these:\n"
                "  * start llama-server:  llama-server -m <model.gguf> --port 8080 -c 16384 -ngl 99\n"
                "  * or correct [llamacpp] base_url in config.toml (a bare address, no path), "
                "then run `devmind doctor`\n"
                "  * or try the UI without a model: run `devmind serve --demo`"
                f"{proxy_hint}"
            )
        snippet = " ".join((body or "").split())[:300]
        return f"{where} with HTTP {status}: {snippet or 'no response body'}" + (
            "\nRun `devmind doctor` to see what that address actually serves."
        )

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
