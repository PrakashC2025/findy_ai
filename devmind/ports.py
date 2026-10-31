"""What is actually listening on this machine's localhost?

The single most confusing failure in DevMind is an HTTP 404 that is an HTML
page: the address in ``[llamacpp] base_url`` belongs to some other program.
This module answers "which port has a model?" without guessing, using the same
no-proxy rule as the local backend.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from .backends.llamacpp import looks_like_html, page_title

LLAMA = "llama.cpp"
OPENAI = "openai-compatible"
WEB = "other web app"
CLOSED = "closed"


@dataclass(frozen=True)
class PortReport:
    port: int
    kind: str
    detail: str

    @property
    def free(self) -> bool:
        return self.kind == CLOSED

    @property
    def usable(self) -> bool:
        return self.kind in {LLAMA, OPENAI}


def _model_ids(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    entries = payload.get("data")
    if not isinstance(entries, list):
        return []
    return [str(entry["id"]) for entry in entries if isinstance(entry, dict) and entry.get("id")]


async def probe_port(port: int, *, transport: httpx.BaseTransport | None = None) -> PortReport:
    """Classify one port: llama.cpp, another OpenAI server, another web app, or free."""
    timeout = httpx.Timeout(1.0, connect=0.4)
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}",
        timeout=timeout,
        trust_env=False,
        transport=transport,
    ) as client:
        try:
            models = await client.get("/v1/models")
        except httpx.HTTPError:
            return PortReport(port, CLOSED, "nothing is listening")

        ids = []
        if models.status_code == 200:
            try:
                ids = _model_ids(models.json())
            except ValueError:
                ids = []
        if ids:
            listed = ", ".join(ids[:3]) + (" …" if len(ids) > 3 else "")
            try:
                health = await client.get("/health")
            except httpx.HTTPError:
                health = None
            is_llama = False
            if health is not None and health.status_code == 200:
                try:
                    status = str(health.json().get("status", "")).lower()
                except (ValueError, AttributeError):
                    status = ""
                is_llama = status in {"ok", "no slot available", "loading model"}
            kind = LLAMA if is_llama else OPENAI
            return PortReport(port, kind, f"{len(ids)} model(s): {listed}")

        # Not a model server. Ask for the root page so the user can recognise it.
        try:
            root = await client.get("/")
        except httpx.HTTPError:
            return PortReport(port, WEB, f"HTTP {models.status_code} on /v1/models")
        bits = [f"HTTP {root.status_code} on /"]
        if root.headers.get("server"):
            bits.append(f"Server: {root.headers['server']}")
        if looks_like_html(root.text) and page_title(root.text):
            bits.append(f"title {page_title(root.text)!r}")
        return PortReport(port, WEB, " · ".join(bits))


async def scan(
    ports: list[int] | range, *, transport: httpx.BaseTransport | None = None
) -> list[PortReport]:
    return list(await asyncio.gather(*(probe_port(port, transport=transport) for port in ports)))
