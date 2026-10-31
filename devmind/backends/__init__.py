"""Inference backends.

DevMind does not care *where* a model runs, only that it can stream text.
A backend is three methods:

    list_models() -> list[str]
    health()      -> Health
    chat(messages, model, options=..., keep_alive=...) -> AsyncIterator[str]

Implementations
---------------
* :class:`LlamaCppBackend` - a user-owned llama.cpp server (home PC / tunnel).
* :class:`OllamaBackend`    - local Ollama, the easiest desktop option.
* :class:`DemoBackend`      - deterministic placeholder for tests and `--demo`.

The PWA runs an on-device model (WebLLM / wllama) and implements the same
contract in JavaScript; the shared prompts and memory travel with the app, so
the browser doesn't need Python to answer.
"""

from __future__ import annotations

from .base import Backend, BackendUnavailable, Health, Message
from .demo import DemoBackend
from .llamacpp import LlamaCppBackend
from .ollama import OllamaBackend

__all__ = [
    "Backend",
    "BackendUnavailable",
    "DemoBackend",
    "Health",
    "LlamaCppBackend",
    "Message",
    "OllamaBackend",
    "get_backend",
]


def get_backend(config, *, demo: bool = False, transport=None) -> Backend:
    """Build the backend described by ``config``.

    ``demo`` (or offline mode) always wins so that tests, CI and aeroplane use
    stay deterministic.
    """
    if demo or getattr(config, "offline", False):
        return DemoBackend()
    kind = config.backend.kind
    if kind == "llamacpp":
        return LlamaCppBackend(
            config.llamacpp.base_url,
            model=config.llamacpp.model,
            api_key=config.llamacpp.api_key,
            connect_timeout=config.llamacpp.connect_timeout_s,
            request_timeout=config.llamacpp.request_timeout_s,
            transport=transport,
        )
    if kind == "ollama":
        return OllamaBackend(
            config.ollama.base_url,
            connect_timeout=config.ollama.connect_timeout_s,
            request_timeout=config.ollama.request_timeout_s,
            transport=transport,
        )
    if kind == "demo":
        return DemoBackend()
    raise ValueError(
        f"Unknown backend '{kind}'. Use backend.kind = llamacpp | ollama | demo "
        "in ~/.devmind/config.toml."
    )
