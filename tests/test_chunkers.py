from __future__ import annotations

from devmind.chunkers import chunk_file, language_for, should_index, symbols_for

PYTHON = '''"""Module docstring."""
import os

CONSTANT = 42


class Billing:
    """Billing service."""

    def refund(self, order_id):
        return order_id

    async def charge(self, amount):
        return amount


def helper(value):
    return value * 2
'''


def test_python_chunks_have_exact_lines_and_symbols() -> None:
    chunks = chunk_file(PYTHON, "billing/service.py", window_lines=60)
    kinds = {(chunk.kind, chunk.symbol) for chunk in chunks}
    assert ("class", "Billing") in kinds
    assert ("method", "Billing.refund") in kinds
    assert ("function", "helper") in kinds

    helper = next(chunk for chunk in chunks if chunk.symbol == "helper")
    assert PYTHON.splitlines()[helper.start_line - 1].startswith("def helper")
    assert helper.text.startswith("def helper")


def test_python_symbols() -> None:
    names = {symbol.name for symbol in symbols_for(PYTHON, "billing/service.py")}
    assert {"Billing", "refund", "charge", "helper"} <= names


def test_generic_chunker_finds_javascript_functions() -> None:
    source = "\n".join(
        [
            "const express = require('express');",
            "",
            "function health(req, res) {",
            "  res.json({ ok: true });",
            "}",
            "",
            "class Router {",
            "  handle(req) { return health(req, {}); }",
            "}",
        ]
    )
    chunks = chunk_file(source, "server.js")
    symbols = {chunk.symbol for chunk in chunks}
    assert "health" in symbols
    assert any(chunk.kind == "class" for chunk in chunks)
    health = next(chunk for chunk in chunks if chunk.symbol == "health")
    assert source.splitlines()[health.start_line - 1].startswith("function health")


def test_generic_chunker_falls_back_to_windows() -> None:
    source = "\n".join(f"line {index}" for index in range(1, 151))
    chunks = chunk_file(source, "notes.txt")  # unknown language, no declarations
    assert len(chunks) >= 2
    assert chunks[0].start_line == 1
    assert all(chunk.end_line >= chunk.start_line for chunk in chunks)


def test_broken_python_does_not_raise() -> None:
    source = "def broken(:\n    pass\n"
    chunks = chunk_file(source, "broken.py")
    assert isinstance(chunks, list)


def test_long_functions_are_split_under_the_character_budget() -> None:
    body = "\n".join(f"    value_{index} = {index}" for index in range(200))
    source = f"def big():\n{body}\n"
    chunks = chunk_file(source, "big.py")
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 2400 for chunk in chunks)
    assert all(chunk.symbol == "big" for chunk in chunks)


def test_language_detection_and_filtering() -> None:
    assert language_for("a/b/c.tsx") == "typescript"
    assert should_index("main.go")
    assert not should_index("image.png")
