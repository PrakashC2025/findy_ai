"""An explicitly opt-in OpenAI-compatible provider.

This exists so a user can plug in **their own** free-tier endpoint - Groq's free
tier, OpenRouter's ``:free`` models, LM Studio on another machine, a work GPU -
without DevMind growing a provider SDK or shipping anyone's key.

It is off by default and needs three things before it will send a single token:

1. ``[remote] enabled = true``,
2. ``[remote] consent = true`` (you know code leaves this machine),
3. an API key in the environment variable named by ``api_key_env``.

``offline = true`` always wins and blocks the provider entirely. Errors are
phrased so the fix is obvious, and the key never appears in logs or errors.
"""

from __future__ import annotations

import os
from typing import Any

from ..registry import Model, Registry
from .base import Health
from .llamacpp import LlamaCppBackend


class RemoteBackend(LlamaCppBackend):
    """OpenAI-compatible HTTP backend for a user-supplied free endpoint."""

    name = "remote"
    label = "remote provider"
    single_model = False

    def __init__(
        self,
        base_url: str,
        *,
        model: str = "",
        api_key: str = "",
        provider: str = "",
        problem: str = "",
        connect_timeout: float = 10.0,
        request_timeout: float = 180.0,
        transport: Any = None,
    ):
        super().__init__(
            base_url or "http://127.0.0.1:1",
            model=model,
            api_key=api_key,
            connect_timeout=connect_timeout,
            request_timeout=request_timeout,
            transport=transport,
        )
        self.provider = provider or "remote provider"
        self.problem = problem

    # ------------------------------------------------------------------ status
    def _guard(self) -> None:
        if self.problem:
            from .base import BackendUnavailable

            raise BackendUnavailable(self.problem)

    async def list_models(self) -> list[str]:
        self._guard()
        return await super().list_models()

    async def health(self) -> Health:
        if self.problem:
            return Health(False, self.problem)
        try:
            models = await super().list_models()
        except Exception as exc:  # BackendUnavailable carries the useful text
            return Health(False, str(exc))
        return Health(
            True,
            f"{self.provider} at {self._http.base_url} (code leaves this device)",
            models=len(models),
        )

    async def chat(self, messages, model: str, *, options=None, keep_alive=None):
        self._guard()
        async for chunk in super().chat(messages, model, options=options, keep_alive=keep_alive):
            yield chunk

    def explain(self, exc: Exception) -> str:
        return (
            f"Cannot reach {self.provider} at {self._http.base_url}.\n"
            "Check the network, that the key in [remote] api_key_env is valid, and the base_url; "
            "or set [remote] enabled = false to fall back to a local model.\n"
            f"Underlying error: {type(exc).__name__}: {exc}"
        )

    # ------------------------------------------------------------------- setup
    @staticmethod
    def from_config(config: Any, *, transport: Any = None) -> RemoteBackend:
        settings = config.remote
        key = os.environ.get(settings.api_key_env, "").strip()
        problem = ""
        if getattr(config, "offline", False):
            problem = (
                "offline mode is on; the remote provider is disabled until you set offline = false"
            )
        elif not settings.enabled:
            problem = "the remote provider is disabled (set [remote] enabled = true)"
        elif not settings.consent:
            problem = (
                "remote provider needs explicit consent: set [remote] consent = true "
                "(code and prompts will leave this machine)"
            )
        elif not settings.base_url:
            problem = "set [remote] base_url to an OpenAI-compatible endpoint, e.g. https://api.groq.com/openai/v1"
        elif not key:
            problem = (
                f"no API key found: export {settings.api_key_env}=... "
                "(DevMind never stores provider keys in its config file)"
            )
        return RemoteBackend(
            settings.base_url,
            model=(settings.models[0] if settings.models else ""),
            api_key=key,
            provider=settings.provider,
            problem=problem,
            connect_timeout=settings.connect_timeout_s,
            request_timeout=settings.request_timeout_s,
            transport=transport,
        )


def register_remote_models(registry: Registry, config: Any) -> None:
    """Give the router real entries for the configured remote models."""
    settings = config.remote
    if not (settings.enabled and settings.consent and settings.models):
        return
    if getattr(config, "offline", False):
        return
    if not os.environ.get(settings.api_key_env, "").strip():
        return  # nothing is routable without the user's own key
    for model_id in settings.models:
        registry.upsert(
            Model(
                id=model_id,
                family=settings.provider or "remote free tier",
                params_b=0.0,
                size_gb=0.0,
                roles=("strong-coder", "reasoning", "judge"),
                quality=max(0.0, min(100.0, settings.model_quality)),
                context=settings.context,
                license="provider terms (free tier)",
                training_ok=False,
                strengths=("free-tier provider", "stronger than a small local model"),
                notes="Configured in [remote]; code leaves this machine when used.",
                cloud=True,
            )
        )
