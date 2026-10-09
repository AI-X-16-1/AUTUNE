"""``service.stated_dates_are_decisions`` -- the fixed rule after the classifier.

A line the classifier left unlabelled that announces the date of a milestone
is a decision (the user, 2026-10-09). No session and no model. See
``test_pipeline_classify.py`` for the rule wired into the task, where the
decision row it makes is visible.
"""

from __future__ import annotations

from datetime import date

import pytest

from autune_contracts.enums import UtteranceKind
from autune_extraction.decisions import ClassifiedUtterance
from autune_extraction.service import STATED_DATE_CONFIDENCE, stated_dates_are_decisions

K = UtteranceKind
DAY = date(2026, 10, 8)  # a Thursday


def utterance(text: str, kind: UtteranceKind | None = None, **more: object) -> ClassifiedUtterance:
    return ClassifiedUtterance(id="utt_1", kind=kind, confidence=0.0, text=text, **more)  # type: ignore[arg-type]


def after(text: str, kind: UtteranceKind | None = None) -> ClassifiedUtterance:
    [result] = stated_dates_are_decisions([utterance(text, kind)], day=DAY)
    return result


@pytest.mark.parametrize(
    "text",
    [
        # The line the cloud classifier left unlabelled in the run that was looked at.
        "3분기 리포트 제출 마감은 10월 30일까지입니다.",
        "출시는 10월 20일로 확정입니다.",
        "마감은 다음 주 금요일이에요.",
        "정식 출시일은 12월 1일입니다.",
        "베타 오픈은 11월 3일입니다.",
        "배포는 매주 화요일입니다.",
        "납기는 11월 말입니다.",
        "데드라인은 이번 주 금요일입니다.",
        "기한은 10월 30일입니다.",
        "릴리스는 다음 주 수요일입니다.",
        # The date something was set to: the past verb is the settling.
        "계약 갱신일은 11월 15일로 확정됐습니다.",
        "마감은 10월 30일로 미뤘습니다.",
        "배포는 화요일로 정했습니다.",
        # A day that does not exist is still a date somebody announced.
        "출시는 2월 30일입니다.",
    ],
)
def test_an_unlabelled_line_that_announces_a_milestones_date_is_a_decision(text: str) -> None:
    result = after(text)

    assert result.kind is K.DECISION
    assert result.confidence == STATED_DATE_CONFIDENCE
    assert result.text == text, "the words are never changed"
    assert result.part == "", "the whole line is the announcement"


@pytest.mark.parametrize(
    "text",
    [
        # No milestone.
        "금요일까지 하겠습니다.",
        "화요일로 가는 버스가 없어요.",
        "날씨가 많이 추워졌네요.",
        # A milestone and no date.
        "마감은 아직 못 정했습니다.",
        "오픈 시간은 오전 9시입니다.",
        # Said of the past: a date recalled or reported, not announced.
        "원래 마감은 10월 30일이었죠.",
        "마감은 10월 1일이었습니다.",
        "지난 배포는 화요일에 했습니다.",
        "10월 20일에 출시했습니다.",
        # Asking for the date.
        "마감이 다음 주 금요일인가요?",
        "출시가 10월 20일 맞나요?",
        "출시일은 10월 20일인가요.",
        # Saying it is open.
        "확정된 건 아직 없고 다음 주에 다시 얘기하죠.",
        "출시일은 아직 미정이고 다음 주에 정하죠.",
        # The two words in two sentences announce nothing.
        "출시 준비는 끝났습니다. 금요일에 뵙겠습니다.",
    ],
)
def test_a_line_that_announces_no_date_is_left_unlabelled(text: str) -> None:
    assert after(text) == utterance(text)


@pytest.mark.parametrize(
    "text",
    [
        # The cost of a rule that reads words: these settle nothing new, and a
        # person rejects them on the review screen.
        "출시가 금요일인데 걱정이네요.",
        "원래 마감은 10월 30일로 잡혀 있었죠.",
    ],
)
def test_what_the_rule_takes_that_a_person_would_not(text: str) -> None:
    assert after(text).kind is K.DECISION


@pytest.mark.parametrize(
    "kind", [K.COMMITMENT, K.OPEN_QUESTION, K.CONCERN, K.AMBIGUOUS, K.DECISION]
)
def test_a_line_the_classifier_labelled_keeps_its_kind(kind: UtteranceKind) -> None:
    """A promise with a deadline in it stays an item and nothing else: its
    due date does not come out a second time as a decision."""
    said = ClassifiedUtterance(
        id="utt_1", kind=kind, confidence=0.7, text="출시 자료는 10월 20일까지 제가 마감하겠습니다."
    )

    assert stated_dates_are_decisions([said], day=DAY) == [said]


def test_the_sentence_is_quoted_when_the_line_says_more() -> None:
    result = after("네 좋습니다. 마감은 10월 30일입니다.")

    assert result.kind is K.DECISION
    assert result.part == "마감은 10월 30일입니다."
    assert result.part in result.text


def test_a_turn_with_no_text_is_never_read() -> None:
    """A speaker who did not consent is a turn with no words."""
    silent = utterance("")

    assert stated_dates_are_decisions([silent], day=DAY) == [silent]


def test_a_turn_the_classifier_read_in_pieces_is_left_as_it_read_it() -> None:
    long_turn = utterance(
        "출시는 10월 20일로 확정입니다. 그리고 나머지는 다음에 얘기하죠.",
        pieces=(
            ("출시는 10월 20일로 확정입니다.", None),
            ("그리고 나머지는 다음에 얘기하죠.", None),
        ),
    )

    assert stated_dates_are_decisions([long_turn], day=DAY) == [long_turn]


def test_a_relative_date_needs_no_meeting_day_to_be_an_announcement() -> None:
    [result] = stated_dates_are_decisions([utterance("마감은 다음 주 금요일이에요.")], day=None)

    assert result.kind is K.DECISION


def test_every_line_comes_back_in_order() -> None:
    lines = [
        utterance("출시는 10월 20일로 확정입니다."),
        ClassifiedUtterance(id="utt_2", kind=K.COMMITMENT, confidence=0.9, text="제가 하겠습니다"),
        ClassifiedUtterance(id="utt_3", kind=None, confidence=0.0, text="네"),
    ]

    result = stated_dates_are_decisions(lines, day=DAY)

    assert [(u.id, u.kind) for u in result] == [
        ("utt_1", K.DECISION),
        ("utt_2", K.COMMITMENT),
        ("utt_3", None),
    ]
