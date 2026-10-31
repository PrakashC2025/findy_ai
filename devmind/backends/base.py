"""Backend contract and shared HTTP plumbing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol
from collections.abc import AsyncIterator

import httpx

Message = dict[str, str]


class BackendUnavailable(RuntimeError):
    """The local inference server cannot be reached or returned an error."""


@dataclass
class Health:
    ok: bool
    detail: str
    version: str = ""
    models: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "version": self.version,
            "models": self.models,
        }


class Backend(Protocol):
    name: str

    async def list_models(self) -> list[str]: ...

    async def health(self) -> Health: ...

    async def chat(
        self,
        messages: list[Message],
        model: str,
        *,
        options: dict[str, Any] | None = None,
        keep_alive: str | None = None,
    ) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...


class LoopClient:
    """An ``httpx.AsyncClient`` that survives ``asyncio.run()`` boundaries.

    The CLI uses one event loop per command (and per chat turn).  httpx clients
    are bound to the loop that created them, so a long-lived client would raise
    "Event loop is closed" on the second command.  Rebinding is cheap and keeps
    the backend usable from tests, CLI and the future API server alike.
    """

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        connect_timeout: float = 3.0,
        request_timeout: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.headers = {"User-Agent": "devmind/0.2", **(headers or {})}
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.transport = transport
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._client.is_closed or self._loop is not loop:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=httpx.Timeout(self.request_timeout, connect=self.connect_timeout),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
                transport=self.transport,
            )
            self._loop = loop
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()


def chunks(text: str, size: int = 18) -> list[str]:
    """Split text into small pieces for the demo backend's fake stream."""
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
