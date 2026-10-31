"""Confidence must come from evidence, not from the model's tone."""

from __future__ import annotations

from dataclasses import dataclass

from devmind.confidence import assess


@dataclass
class FakeCheck:
    ok: bool
    skipped: bool = False


def test_demo_mode_cannot_be_confident() -> None:
    result = assess("anything", demo=True)
    assert result.label == "none"
    assert result.score < 0.1


def test_passing_checks_raise_confidence_and_failures_lower_it() -> None:
    passing = assess("fixed it", check_results=[FakeCheck(True), FakeCheck(True)])
    failing = assess("fixed it", check_results=[FakeCheck(False)])
    none = assess("fixed it")
    assert passing.score > none.score > failing.score
    assert passing.label in {"high", "medium"}


def test_citations_raise_confidence() -> None:
    plain = assess("the router picks by score")
    cited = assess("the router picks by score [C1] [C2]", sources=2)
    assert cited.score > plain.score
    assert any("cited" in reason for reason in cited.reasons)


def test_links_not_in_sources_are_penalised() -> None:
    honest = assess("see https://docs.python.org/3/", source_urls=["https://docs.python.org/3/"])
    invented = assess(
        "see https://made-up-docs.example/x", source_urls=["https://docs.python.org/3/"]
    )
    assert invented.score < honest.score
    assert any("not present in the retrieved sources" in reason for reason in invented.reasons)


def test_uncertainty_is_reported_as_such() -> None:
    result = assess("I am not sure, the flag may not exist")
    assert any("uncertainty" in reason for reason in result.reasons)
    assert result.label in {"low", "medium"}


def test_skipped_only_checks_do_not_count_as_verified() -> None:
    result = assess("answer", check_results=[FakeCheck(False, skipped=True)])
    assert any("no automated checks" in reason for reason in result.reasons)


def test_score_is_bounded() -> None:
    best = assess("x [C1]", sources=10, check_results=[FakeCheck(True)] * 5, rounds=2)
    assert 0.02 <= best.score <= 0.97
    worst = assess(
        "not sure " + "https://fake.example/a " * 5, check_results=[FakeCheck(False)] * 5
    )
    assert worst.score >= 0.02
