"""Difficulty classification gates reasoning spend and repair budgets."""

from __future__ import annotations

from devmind.difficulty import HARD, NORMAL, TRIVIAL, classify_difficulty


def test_short_factual_question_is_trivial() -> None:
    result = classify_difficulty("What is a Python list comprehension?")
    assert result.level == TRIVIAL
    assert result.score < 20
    assert result.budget_s() == 15.0
    assert not result.needs_reasoning


def test_traceback_is_hard() -> None:
    prompt = (
        "Fix this failure:\nTraceback (most recent call last):\n"
        '  File "app.py", line 4, in <module>\nValueError: bad value\n'
        "It happens after refactoring the async transaction handling in "
        "src/app.py, src/db.py and src/http.py."
    )
    result = classify_difficulty(prompt)
    assert result.level == HARD
    assert result.needs_reasoning
    assert result.budget_s() > 200
    assert any("traceback" in reason for reason in result.reasons)


def test_normal_request_stays_normal() -> None:
    prompt = "Add an endpoint that returns the list of invoices for the current user."
    result = classify_difficulty(prompt)
    assert result.level == NORMAL
    assert 0 <= result.score < 55


def test_long_multi_file_request_escalates() -> None:
    prompt = (
        "Refactor the order flow. Context: api/orders.py handles the request, "
        "services/billing.py charges the card, models/order.py stores state. "
        "It must stay backwards compatible. " + "detail " * 120
    )
    result = classify_difficulty(prompt)
    assert result.level == NORMAL or result.level == HARD
    assert result.score >= 30


def test_error_output_alone_can_raise_difficulty() -> None:
    quiet = classify_difficulty("why is the build red?")
    loud = classify_difficulty(
        "why is the build red?",
        error_output="Traceback (most recent call last):\nValueError: nope",
    )
    assert loud.score > quiet.score
