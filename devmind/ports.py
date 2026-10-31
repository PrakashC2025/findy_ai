"""What is actually listening on this machine's localhost?

The single most confusing failure in DevMind is an HTTP 404 that is an HTML
page: the address in ``[llamacpp] base_url`` belongs to some other program.
This module answers "which port has a model?" without guessing, using the same
no-proxy rule as the local backend.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx

from .backends.llamacpp import looks_like_html, page_title

LLAMA = "llama.cpp"
OPENAI = "openai-compatible"
WEB = "other web app"
CLOSED = "closed"

# Friendly names for the `Server` header, so "Apache" becomes actionable.
SERVER_NOTES = {
    "apache": "Apache web server (often XAMPP/WAMP) - leave it alone",
    "nginx": "nginx web server - leave it alone",
    "microsoft-iis": "Microsoft IIS - leave it alone",
    "uvicorn": "a Python/uvicorn app - probably DevMind itself",
    "simplehttp": "Python's http.server, not a model",
    "gunicorn": "a Python web app, not a model",
    "werkzeug": "a Flask app, not a model",
}

# Ports worth checking when nothing is configured correctly. 8080 is llama.cpp's
# usual port, 11434 is Ollama, 1234 is LM Studio.
DEFAULT_PORTS = (*range(8080, 8091), 11434, 1234)


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


def server_note(server: str) -> str:
    """A short human explanation for a ``Server`` header."""
    value = (server or "").lower()
    for needle, note in SERVER_NOTES.items():
        if needle in value:
            return note
    return ""


def _model_ids(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    entries = payload.get("data")
    if not isinstance(entries, list):
        return []
    return [str(entry["id"]) for entry in entries if isinstance(entry, dict) and entry.get("id")]


def probe_port(port: int, *, transport: httpx.BaseTransport | None = None) -> PortReport:
    """Classify one port: llama.cpp, another OpenAI server, another web app, or free."""
    timeout = httpx.Timeout(1.0, connect=0.4)
    with httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        timeout=timeout,
        trust_env=False,
        transport=transport,
    ) as client:
        try:
            models = client.get("/v1/models")
        except httpx.HTTPError:
            return PortReport(port, CLOSED, "nothing is listening")

        ids: list[str] = []
        if models.status_code == 200:
            try:
                ids = _model_ids(models.json())
            except ValueError:
                ids = []
        if ids:
            listed = ", ".join(ids[:3]) + (" …" if len(ids) > 3 else "")
            try:
                health = client.get("/health")
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
            root = client.get("/")
        except httpx.HTTPError:
            return PortReport(port, WEB, f"HTTP {models.status_code} on /v1/models")
        bits = [f"HTTP {root.status_code} on /"]
        if root.headers.get("server"):
            bits.append(f"Server: {root.headers['server']}")
            note = server_note(root.headers["server"])
            if note:
                bits.append(note)
        if looks_like_html(root.text) and page_title(root.text):
            bits.append(f"title {page_title(root.text)!r}")
        return PortReport(port, WEB, " · ".join(bits))


def scan(
    ports: list[int] | range | tuple[int, ...], *, transport: httpx.BaseTransport | None = None
) -> list[PortReport]:
    """Probe several ports in parallel, preserving the order asked for."""
    ports = list(ports)
    if not ports:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(ports))) as pool:
        return list(pool.map(lambda port: probe_port(port, transport=transport), ports))


def find_local_model(
    ports: list[int] | range | tuple[int, ...] = DEFAULT_PORTS,
    *,
    transport: httpx.BaseTransport | None = None,
) -> PortReport | None:
    """The first usable local model server, preferring llama.cpp over other APIs."""
    reports = scan(ports, transport=transport)
    for report in reports:
        if report.kind == LLAMA:
            return report
    return next((report for report in reports if report.kind == OPENAI), None)


def port_of(url: str) -> int | None:
    """The port of a base URL, defaulting like the scheme (http 80, https 443)."""
    from urllib.parse import urlparse

    parsed = urlparse(url if "//" in url else f"//{url}")
    if parsed.port:
        return parsed.port
    return {"http": 80, "https": 443}.get(parsed.scheme or "http")
