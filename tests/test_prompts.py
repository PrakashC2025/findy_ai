from __future__ import annotations

from devmind.config import ModelConfig
from devmind.memory.retriever import MemoryContext
from devmind.memory.store import Hit
from devmind.prompts import build_messages, build_system_prompt
from devmind.router import classify


def test_system_prompt_contains_the_hard_rules() -> None:
    profile = classify("write a python function that parses csv")
    prompt = build_system_prompt(profile, MemoryContext(), ModelConfig())
    assert "Never invent APIs" in prompt
    assert "Do exactly what was asked" in prompt
    assert "write_new" in prompt
    assert "python" in prompt
    assert "How to verify" in prompt  # output format for code tasks


def test_memory_is_cited() -> None:
    profile = classify("add a dependency")
    hit = Hit(id="mem_1", kind="preference", text="We use pnpm, never npm", score=0.8)
    context = MemoryContext(
        block="What you already know:\n[M1] (preference) We use pnpm, never npm",
        hits=[hit],
        citations=[{"id": "M1", "kind": "preference", "text": hit.text}],
    )
    prompt = build_system_prompt(profile, context, ModelConfig())
    assert "[M1]" in prompt
    assert "cite it like [m1]" in prompt.lower()


def test_build_messages_keeps_history_and_ends_with_user() -> None:
    profile = classify("fix this traceback")
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "tool", "content": "ignored"},
    ]
    messages = build_messages(
        "now fix it",
        profile=profile,
        context=MemoryContext(),
        config=ModelConfig(),
        history=history,
    )
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "now fix it"}
    assert [message["role"] for message in messages[1:-1]] == ["user", "assistant"]


def test_user_system_extra_is_appended() -> None:
    profile = classify("explain this")
    config = ModelConfig(system_extra="Answer in British English.")
    prompt = build_system_prompt(profile, MemoryContext(), config)
    assert "British English" in prompt
