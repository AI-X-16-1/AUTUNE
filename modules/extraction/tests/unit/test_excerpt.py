"""Where a part sits in an utterance, and what is cut from it to show.

The rules under test: a part is found whatever happened to the whitespace at a
cut, what is shown is the stored text between two offsets and nothing else, and
anything that does not fit -- a part that is not there, offsets counted on
another text -- shows no part rather than a wrong one.
"""

from __future__ import annotations

import pytest

from autune_extraction.excerpt import cut, span_of
from autune_extraction.pipeline.llm import said_lines

TURN = (
    "지난주 배포는 큰 문제 없이 끝났습니다. 다만 결제 쪽 로그가 빠져 있어서\n"
    "설문은 제가 금요일까지 다시 쓰겠습니다. 그리고 출시는 다음 달로 미루기로 했습니다."
)
PROMISE = "설문은 제가 금요일까지 다시 쓰겠습니다."
DECISION = "그리고 출시는 다음 달로 미루기로 했습니다."


def shown(whole: str, *parts: str) -> str | None:
    span = span_of(whole, parts)
    return cut(whole, *span) if span else None


def test_a_part_is_cut_from_the_turn_as_it_was_said() -> None:
    start, end = span_of(TURN, [PROMISE])  # type: ignore[misc]

    assert TURN[start:end] == PROMISE
    assert shown(TURN, PROMISE) == PROMISE


def test_spacing_changed_at_a_cut_does_not_hide_the_part() -> None:
    """A short sentence is joined to the next with one space, whatever was
    between them; what is shown has the turn's own spacing."""
    joined = "다만 결제 쪽 로그가 빠져 있어서 설문은 제가 금요일까지 다시 쓰겠습니다."

    assert shown(TURN, joined) == "다만 결제 쪽 로그가 빠져 있어서\n" + PROMISE


def test_every_line_a_long_turn_is_read_in_is_found_in_it() -> None:
    """The lines the classifier is asked about are the parts that get recorded."""
    turn = " ".join([TURN.replace("\n", " ")] * 3)

    lines = said_lines(turn, None)

    assert len(lines) > 1
    for line in lines:
        start, end = span_of(turn, [line])  # type: ignore[misc]
        assert "".join(turn[start:end].split()) == "".join(line.split())


def test_several_parts_are_one_span_from_the_first_to_the_last() -> None:
    assert shown(TURN, DECISION, PROMISE) == f"{PROMISE} {DECISION}"


@pytest.mark.parametrize(
    "parts",
    [
        pytest.param(["회의실은 다음 달부터 예약제로 합니다"], id="not in the turn"),
        pytest.param([PROMISE, "회의실은 예약제로 합니다"], id="one of two not in the turn"),
        pytest.param([TURN], id="all of the turn"),
        pytest.param([f"  {TURN}\n"], id="all of the turn, spaced otherwise"),
        pytest.param([], id="no part"),
        pytest.param(["  "], id="a blank part"),
    ],
)
def test_no_span_when_there_is_no_part_to_show(parts: list[str]) -> None:
    assert span_of(TURN, parts) is None


def test_an_empty_turn_has_no_part() -> None:
    assert span_of("", [PROMISE]) is None


@pytest.mark.parametrize(
    ("start", "end"),
    [
        pytest.param(None, None, id="none recorded"),
        pytest.param(0, len(TURN), id="all of it"),
        pytest.param(10, len(TURN) + 1, id="past the end"),
        pytest.param(20, 20, id="empty"),
        pytest.param(30, 20, id="backwards"),
        pytest.param(-5, 20, id="negative"),
    ],
)
def test_nothing_is_cut_when_the_offsets_do_not_give_a_part(
    start: int | None, end: int | None
) -> None:
    assert cut(TURN, start, end) is None


def test_what_is_cut_is_the_stored_text_and_nothing_is_added() -> None:
    start = TURN.index(PROMISE)

    part = cut(TURN, start, start + len(PROMISE))

    assert part == PROMISE and part in TURN
