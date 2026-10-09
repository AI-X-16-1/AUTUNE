"""``service.stated_dates_are_decisions`` -- the fixed rule after the classifier.

A line the classifier left unlabelled that announces the date of a milestone
is a decision (module B's owner, 2026-10-09). No session and no model. See
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


# --- narrowed (module B's owner, 2026-10-09: "가장 좁게") -------------------------
#
# A line taken wrongly is a decision nobody made, and D reads B's pending
# decisions as they come (mminjae97 on #1145). A line left alone is where it was
# before the rule. The first two below were the rule's known cost until then.


@pytest.mark.parametrize(
    "text",
    [
        # A past form with an ending that recalls or asks agreement.
        "원래 마감은 10월 30일로 잡혀 있었죠.",
        "출시일은 10월 20일로 확정됐잖아요.",
        "마감은 10월 30일까지였죠.",
        "마감은 10월 30일까지였는데 다들 놓쳤습니다.",
        "출시는 10월 20일로 확정됐거든요.",
        "출시는 10월 20일로 확정됐는데 QA 일정이 남았습니다.",
        # A state that was, and a past of a past.
        "마감은 10월 30일로 예정돼 있었습니다.",
        "출시는 10월 20일로 확정됐었습니다.",
        "지난번에 배포는 화요일로 정했었죠.",
        # A look-back word with a past form, one date named.
        "출시는 원래 10월 20일로 잡았습니다.",
        "당초 오픈은 11월 1일로 계획했습니다.",
        "처음에 출시는 9월 말로 잡았어요.",
    ],
)
def test_a_line_that_looks_back_at_a_date_is_left_unlabelled(text: str) -> None:
    assert after(text) == utterance(text)


@pytest.mark.parametrize(
    "text",
    [
        "계약 갱신일은 11월 15일로 확정됐습니다.",
        "출시는 10월 20일로 확정했습니다.",
        "마감은 10월 30일까지로 확정됐습니다.",
        "출시는 10월 20일로 정해졌습니다.",
        "오픈은 11월 1일로 잡혔습니다.",
        # A state that is, not one that was.
        "오픈은 11월 1일로 잡혀 있습니다.",
        # 있었 that is not a state something was left in.
        "문제가 있었지만 출시는 10월 20일로 확정했습니다.",
        # The plan holds.
        "원래 계획대로 출시는 10월 20일입니다.",
        "원래 계획대로 출시는 10월 20일로 확정했습니다.",
        # A look-back word and nothing past: the date holds now.
        "기존에 안내한 대로 출시는 10월 20일입니다.",
        # A change announced with its old date: two dates.
        "원래 10월 30일이던 마감을 11월 5일로 확정했습니다.",
        "기존에 10월 30일이었던 마감은 11월 5일로 바뀌었습니다.",
        "출시는 10월 20일에서 11월 3일로 미뤄졌습니다.",
    ],
)
def test_a_settling_said_in_the_past_is_still_a_decision(text: str) -> None:
    assert after(text).kind is K.DECISION


@pytest.mark.parametrize(
    "text",
    [
        # Still to be decided.
        "단가랑 오픈일까지 오늘 안에 정해야 되는데 될지 모르겠습니다.",
        "보고서 마감을 봐야 되는데 이게 오늘 제일 급한 건입니다.",
        "출시일은 다음 주에 정해야 합니다.",
        "마감이 금요일인데 맞출지 봐야죠.",
        # A condition.
        "이걸 오늘 못 끝내면 오픈 일정이 또 밀립니다.",
        "QA가 늦어지면 출시는 11월 3일입니다.",
        # A worry.
        "출시가 금요일인데 걱정이네요.",
        "마감이 다음 주 금요일이라 일정이 빡빡합니다.",
        "오픈이 11월 3일인데 시간이 촉박합니다.",
        "출시가 10월 20일이라 좀 불안합니다.",
    ],
)
def test_a_line_that_is_still_open_is_left_unlabelled(text: str) -> None:
    assert after(text) == utterance(text)


@pytest.mark.parametrize(
    "text",
    [
        # An obligation to finish states a deadline.
        "배포는 금요일까지 끝내야 합니다.",
        # 그러면 carries on from what was said; it is no condition on the date.
        "그러면 출시는 10월 20일입니다.",
        "그렇다면 마감은 다음 주 금요일입니다.",
        "아니면 오픈은 11월 3일입니다.",
        "그러면은 출시는 10월 20일입니다.",
        # Summing up is where an announcement often comes.
        "정리하면 출시는 10월 20일입니다.",
        "다시 말씀드리면 마감은 다음 주 금요일입니다.",
        # A reminder in the present is not a recollection: nothing in it is past.
        "마감은 이번 주 금요일이죠.",
        # A noun that ends like a condition.
        "결제 화면 배포는 다음 주 화요일입니다.",
        "반면 앱 출시는 11월 3일입니다.",
        # A deadline being set, said inside something else.
        "답변 기한도 모레까지로 적어서 같이 넣겠습니다.",
        "자료 요청은 오늘 보내고 답변 기한은 모레까지로 잡고",
        "그럼 이번 주는 배포하지 않고 다음 주 수요일에 함께 내보내겠습니다.",
    ],
)
def test_what_stays_a_decision_beside_the_open_lines(text: str) -> None:
    assert after(text).kind is K.DECISION


@pytest.mark.parametrize(
    "text",
    [
        "그럼 거래처에는 수요일에 확정해서 알려드릴게요.",
        "내일 오전에 결과를 보고 납품 날짜 확정,",
    ],
)
def test_the_day_something_will_be_settled_is_not_a_settled_date(text: str) -> None:
    """확정 is the only milestone word, and the date is when the settling
    happens -- not what anything was settled as."""
    assert after(text) == utterance(text)


@pytest.mark.parametrize(
    "text",
    [
        "계약 갱신일은 11월 15일로 확정됐습니다.",
        "일정은 10월 20일로 확정입니다.",
        # Another milestone word beside it: the date is that milestone's.
        "출시는 10월 20일 확정입니다.",
    ],
)
def test_a_date_something_was_settled_as_is_still_a_decision(text: str) -> None:
    assert after(text).kind is K.DECISION


@pytest.mark.parametrize(
    "text",
    [
        # Each was shown to the owner as the price of the narrowing: real, and
        # no longer taken. It stays what the classifier made of it.
        "문제 없으면 출시는 10월 20일입니다.",
        "일정은 10월 20일 확정입니다.",
        "그럼 마감은 10월 30일로 확정됐죠.",
    ],
)
def test_what_the_narrowing_costs(text: str) -> None:
    assert after(text) == utterance(text)


def test_a_worry_said_in_other_words_is_still_taken() -> None:
    """The four worry words are the ones the owner was shown. This line is the
    same kind and has none of them: the cost that is left."""
    assert after("출시가 10월 20일인데 QA가 빠듯합니다.").kind is K.DECISION


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
