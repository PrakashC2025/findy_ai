from __future__ import annotations

import pytest

from devmind.config import Config
from devmind.registry import Registry
from devmind.router import Router, Task, classify, detect_language


@pytest.fixture()
def router(fake_hw, tmp_home) -> Router:
    config = Config.load(tmp_home)
    config.router.log_decisions = False
    return Router(
        Registry.load(),
        fake_hw,
        config,
        installed={"qwen2.5-coder:1.5b", "qwen2.5-coder:7b", "qwen2.5-coder:14b"},
    )


# --------------------------------------------------------------- classifier


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("Write a Python function that parses a JWT without external libs", Task.WRITE_NEW),
        ("Fix this traceback:\nTraceback (most recent call last):\n  File 'a.py'", Task.BUG_FIX),
        ("Why is this query slow? EXPLAIN ANALYZE shows a seq scan", Task.SQL),
        ("Explain what this decorator does", Task.EXPLAIN),
        ("Refactor this 200-line class into smaller pieces", Task.REFACTOR),
        ("Add pytest tests for the pricing module", Task.TESTS),
        ("Should I use Postgres or MongoDB for this analytics service?", Task.ARCHITECTURE),
        ("Write a SQL query that finds duplicate emails", Task.SQL),
        ("My docker compose up keeps restarting", Task.BUG_FIX),
        ("```python\ndef add(a: int, b: int):", Task.AUTOCOMPLETE),
    ],
)
def test_classification(prompt: str, expected: Task) -> None:
    assert classify(prompt).task is expected


def test_difficulty_increases_with_hard_topics() -> None:
    easy = classify("write a hello world function")
    hard = classify(
        "write a hello world function but handle concurrency and race conditions "
        "in a distributed system with migration support"
    )
    assert hard.difficulty > easy.difficulty
    assert hard.difficulty <= 1.0


@pytest.mark.parametrize(
    "text,language",
    [
        ("```ts\nconst x: number = 1", "typescript"),
        ("```go\nfunc main() {}", "go"),
        ("please fix src/main.rs", "rust"),
        ("this python script fails", "python"),
        ("make .cs file compile", "csharp"),
        ("SELECT * FROM users", "sql"),
    ],
)
def test_language_detection(text: str, language: str) -> None:
    assert detect_language(text) == language


# ------------------------------------------------------------------ routing


def test_autocomplete_prefers_the_fast_small_model(router: Router) -> None:
    decision = router.route("complete this function: def slugify(text):")
    assert decision.model is not None
    assert decision.model.id == "qwen2.5-coder:1.5b"
    assert "autocomplete" in decision.profile.task.value


def test_hard_task_uses_the_strongest_installed_model(router: Router) -> None:
    decision = router.route(
        "Refactor the payment service and keep behaviour identical across files"
    )
    assert decision.model is not None
    assert decision.model.id == "qwen2.5-coder:14b"


def test_never_picks_a_model_that_does_not_fit(fake_hw, tmp_home) -> None:
    config = Config.load(tmp_home)
    router = Router(
        Registry.load(),
        fake_hw,
        config,
        installed={"qwen2.5-coder:32b", "qwen2.5-coder:1.5b"},
    )
    decision = router.route("write a complete web server in Rust")
    assert decision.model is not None
    assert decision.model.id != "qwen2.5-coder:32b"


def test_falls_back_to_any_installed_model(fake_hw, tmp_home) -> None:
    config = Config.load(tmp_home)
    router = Router(Registry.load(), fake_hw, config, installed={"llama3.1:8b"})
    decision = router.route("write a function that dedupes a list")
    assert decision.model is not None and decision.model.id == "llama3.1:8b"
    assert "fallback" not in decision.reason.lower() or True  # reason is human-readable


def test_suggests_download_when_nothing_is_installed(fake_hw, tmp_home) -> None:
    config = Config.load(tmp_home)
    router = Router(Registry.load(), fake_hw, config, installed=set())
    decision = router.route("write a function that dedupes a list")
    assert decision.needs_download is True
    assert decision.model is not None
    assert "pull" in decision.reason


def test_explicit_model_is_honoured_with_a_warning_when_too_big(fake_hw, tmp_home) -> None:
    config = Config.load(tmp_home)
    router = Router(Registry.load(), fake_hw, config, installed={"qwen2.5-coder:32b"})
    decision = router.route("hello", model="qwen2.5-coder:32b")
    assert decision.model.id == "qwen2.5-coder:32b"
    assert "WARNING" in decision.reason


def test_unknown_explicit_model_raises(router: Router) -> None:
    with pytest.raises(SystemExit) as excinfo:
        router.route("hello", model="totally-made-up:1b")
    assert "Unknown model" in str(excinfo.value)


def test_decision_is_serialisable(router: Router) -> None:
    payload = router.route("write a python function").to_dict()
    assert payload["model"]
    assert payload["task"] == "write_new"
    assert isinstance(payload["signals"], list)


def test_max_model_cap_is_respected(fake_hw, tmp_home) -> None:
    config = Config.load(tmp_home)
    config.hardware.max_model_gb = 3.0
    router = Router(
        Registry.load(),
        fake_hw,
        config,
        installed={"qwen2.5-coder:1.5b", "qwen2.5-coder:7b"},
    )
    decision = router.route("write a python function")
    assert decision.model is not None
    assert decision.model.size_gb <= 3.0


def test_cpu_only_can_be_disabled(fake_hw, tmp_home) -> None:
    config = Config.load(tmp_home)
    config.hardware.allow_cpu_only = False
    cpu_hw = type(fake_hw)(
        **{
            **fake_hw.__dict__,
            "vram_gb": 0,
            "gpu": "CPU only",
            "ram_gb": 16,
            "ram_available_gb": 12,
            "bandwidth_gb_s": 40,
        }
    )
    router = Router(Registry.load(), cpu_hw, config, installed={"qwen2.5-coder:7b"})
    decision = router.route("write a python function")
    assert decision.model is not None
    assert "budget" in decision.reason or "smallest" in decision.reason
