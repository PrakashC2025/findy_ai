from __future__ import annotations

from pathlib import Path

from devmind.memory.store import MemoryStore
from devmind.security import looks_like_secret, redact, redact_secrets


def test_provider_keys_are_redacted() -> None:
    text = (
        "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456\n"
        "GROQ=gsk_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ\n"
        "GOOGLE=" + "AIza" + "A" * 35 + "\n"
    )
    result = redact(text)
    assert result.changed
    assert "sk-proj-" not in result.text
    assert "gsk_" not in result.text
    assert "AIza" not in result.text
    assert "REDACTED" in result.text


def test_private_keys_and_tokens_are_redacted() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    assert "MIIEow" not in redact_secrets(f"key: {pem}")
    assert redact_secrets("token ghp_" + "a" * 36) == "token [REDACTED:github_token]"


def test_credentials_in_urls_are_redacted() -> None:
    out = redact_secrets("postgres://admin:hunter2@db.internal:5432/app")
    assert "hunter2" not in out
    assert out.startswith("postgres://[REDACTED:credentials]@")


def test_assignments_keep_the_key_name() -> None:
    out = redact_secrets('db_password = "s3cr3t-value"')
    assert "s3cr3t-value" not in out
    assert "db_password" in out


def test_placeholders_and_indirection_are_left_alone() -> None:
    for safe in (
        'password = "changeme"',
        "API_KEY=$MY_KEY",
        "token: your_key_here",
        "this is a normal sentence about passwords",
    ):
        assert not looks_like_secret(safe), safe


def test_memory_writes_are_redacted(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    memory_id = store.remember("Deploy key is ghp_" + "b" * 36, kind="fact")
    stored = store.get_memory(memory_id)
    assert "ghp_" not in stored["text"]
    assert "REDACTED" in stored["text"]

    store.record_episode("s1", "user", "here is my key sk-proj-" + "c" * 32)
    episode = store.recent_episodes("s1")[0]
    assert "sk-proj-" not in episode["content"]


def test_redaction_can_be_disabled(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "m.db", redact=False)
    secret = "sk-proj-" + "d" * 32
    memory_id = store.remember(f"key: {secret}")
    assert store.get_memory(memory_id)["text"] == f"key: {secret}"
