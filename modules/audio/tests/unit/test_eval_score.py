"""One scorer for the CLI and the tests.

The two used to be separate loops that agreed by accident (#179 review): the
CLI failed every run on three rows the tests knew to expect, and the CLI
skipped a row the tests counted. A declared cost lives in the corpus row now,
as ``known_inexact``, and there is one place that reads it.
"""

from __future__ import annotations

from autune_audio.eval import score
from autune_audio.eval.corpus import Row


def row(text: str, masked: str, *, known_inexact: str | None = None, index: int = 0) -> Row:
    return Row(
        text=text,
        masked=masked,
        source="planted",
        categories=("phone",) if masked != text else (),
        note=None,
        index=index,
        known_inexact=known_inexact,
    )


def test_a_declared_row_that_still_differs_is_declared_not_unexplained() -> None:
    rows = [
        row("제 번호 01098765432이에요", "제 번호 010****5432이에요", known_inexact="#158 item 5")
    ]

    report = score.score(rows, masker=lambda _: "제 번호 ************에요")

    assert report.unexplained == []
    assert report.declared == rows
    assert report.fixed == []
    assert report.ok is True


def test_a_declared_row_that_now_matches_has_to_leave_the_declaration() -> None:
    """The list stays honest in both directions: a row that got fixed is a
    failure until somebody removes the declaration."""
    rows = [
        row("제 번호 01098765432이에요", "제 번호 010****5432이에요", known_inexact="#158 item 5")
    ]

    report = score.score(rows, masker=lambda _: "제 번호 010****5432이에요")

    assert report.fixed == rows
    assert report.ok is False


def test_an_undeclared_difference_is_unexplained_and_fails() -> None:
    rows = [row("연락처 010-1234-5678", "연락처 010-****-5678")]

    report = score.score(rows, masker=lambda _: "연락처 010-1234-5678")

    assert report.unexplained == rows
    assert report.ok is False


def test_a_masker_that_changes_the_token_count_costs_precision() -> None:
    """The CLI used to `continue` past the ValueError and the row vanished from
    both the numerator and the denominator -- the one over-masking that merges
    spans across a separator, exactly the bug masking.py:216 fixed, left no
    trace in the number. It counts as spans produced with none correct."""
    rows = [row("연락처 010 1234 5678 입니다", "연락처 010 **** 5678 입니다")]

    aligned = score.score(rows, masker=lambda _: "연락처 010 **** 5678 입니다")
    merged = score.score(rows, masker=lambda _: "연락처 ************* 입니다")  # spaces eaten

    assert aligned.precision == 1.0
    assert merged.precision < 1.0
    assert merged.spans_produced > 0


def test_a_row_is_identified_without_its_text() -> None:
    """What a failure message or a --verbose line may say about a row: where
    it came from, which one it is, what it holds. Never the value -- an
    assertion message goes to a CI log, and the pair of text and masked form
    is what makes a value recoverable (privacy.md section 2)."""
    r = row("연락처 010-1234-5678", "연락처 010-****-5678", index=7)

    assert score.identify(r) == "planted#7 phone"
    assert "010" not in score.identify(r)


def test_the_real_corpus_passes_with_its_declarations() -> None:
    """The CLI's exit code, on the corpus this branch ships. It was 1 on every
    run because the declarations lived only in the tests."""
    from autune_audio.eval.__main__ import main

    assert main([]) == 0
