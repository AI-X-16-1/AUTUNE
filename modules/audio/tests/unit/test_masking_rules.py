"""A team's masking shapes: what becomes a rule, and what a rule masks."""

from __future__ import annotations

import pytest

from autune_audio.masking_rules import apply, shape_of


@pytest.mark.parametrize(
    ("span", "shape"),
    [
        ("A-20391", "A-#####"),
        ("JIRA-1234", "AAAA-####"),
        ("ab12cd", "aa##aa"),
        ("서울2024", "가가####"),
    ],
)
def test_a_span_becomes_its_shape_and_nothing_of_its_text(span: str, shape: str) -> None:
    assert shape_of(span) == shape


@pytest.mark.parametrize(
    "span",
    [
        "박민수",  # no digit: a word shape would mask every three-syllable word
        "A-1",  # too short to be anything but a small number
        "A 20391",  # two tokens
        "a@b.c1234",  # a character that is neither classed nor a separator
    ],
)
def test_spans_that_would_make_a_dangerous_rule_make_none(span: str) -> None:
    assert shape_of(span) is None


def test_a_rule_masks_the_same_shape_with_a_different_value() -> None:
    assert apply("사번 B-77812 확인", ["A-#####"]) == "사번 *-***** 확인"


def test_a_korean_particle_after_the_value_does_not_stop_the_match() -> None:
    assert apply("A-20391로 등록", ["A-#####"]) == "*-*****로 등록"


def test_a_match_inside_a_longer_token_is_left_alone() -> None:
    assert apply("XA-203915", ["A-#####"]) == "XA-203915"


def test_no_rules_change_nothing() -> None:
    assert apply("A-20391", []) == "A-20391"
