"""The minutes page B hands the assistant, on the strings the web's copy is tested on.

``apps/web/src/features/actions/components/CopyMinutes.test.tsx`` tests
``minutesText``; each test here is one of those, with the same summary and the
same expected text, so the two renderings stay in step. Two of the web's have no
counterpart: a due date that is not a day cannot reach the server's
``ActionItemRead`` (its ``due_date`` is a ``date``), and the clipboard is the
browser's. What is here and not there is the screening, which only this page
needs: it leaves B.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from autune_extraction.minutes import minutes_page, title_of
from autune_extraction.schemas import (
    ActionItemRead,
    GeneratedSummary,
    MeetingSummary,
    SummaryDecision,
)

STARTED = datetime(2026, 10, 8, 3, 0, tzinfo=UTC)
"""``2026-10-08T03:00:00Z``: noon on Thursday in Korea, as in the web's tests."""


def item(**over: Any) -> ActionItemRead:
    base: dict[str, Any] = {
        "id": "act",
        "meeting_id": "mtg_1",
        "meeting_title": "주간 회의",
        "description": "스펙 초안 공유",
        "status": "todo",
        "confidence": 0.9,
        "is_candidate": False,
        "origin": "model",
        "assignee_id": None,
        "assignee_label": None,
        "assignee_name": None,
        "due_date": None,
        "source_utterance_ids": ["utt_1"],
        "sync_refs": [],
    }
    return ActionItemRead(**(base | over))


def decision(id: str, statement: str, status: str = "confirmed") -> SummaryDecision:
    return SummaryDecision(id=id, statement=statement, status=status)  # type: ignore[arg-type]


SUMMARY = MeetingSummary(
    meeting_id="mtg_1",
    decisions=[
        decision("dec_1", "배포는 다음 주 화요일에 한다"),
        decision("dec_2", "검색 개편은 2주 미룬다", "pending"),
    ],
    action_items=[
        item(id="a1", assignee_name="김민경", due_date=date(2026, 10, 9), status="in_progress"),
        item(
            id="a2", description="QA 일정 확인", assignee_label="민구", status="needs_confirmation"
        ),
        item(id="a3", description="회고 자료 정리", status="done"),
        item(id="a4", description="모델이 확신하지 못한 것", is_candidate=True),
    ],
    open_questions=1,
    ambiguous_waiting=0,
    note="  다음 회의는 목요일.  ",
)


def text(summary: MeetingSummary, title: str | None = None) -> str:
    return minutes_page(summary, title).text


def with_(**over: Any) -> MeetingSummary:
    return SUMMARY.model_copy(update=over)


def test_does_not_call_an_item_closed_without_being_finished_done() -> None:
    page = text(
        with_(
            action_items=[
                item(id="a9", description="외주 견적 받기", status="done", closed_unfinished=True),
                item(id="a10", description="회고 자료 정리", status="done"),
            ]
        )
    )

    assert "1. 외주 견적 받기 — 담당 미지정 · 기한 없음 · 끝내지 않고 닫힘" in page
    assert "2. 회고 자료 정리 — 담당 미지정 · 기한 없음 · 완료" in page


def test_keeps_an_item_or_a_decision_typed_over_several_lines_on_its_numbered_line() -> None:
    page = text(
        with_(
            decisions=[decision("dec_9", "가격은 12,000원\n  수량은 30개")],
            action_items=[item(id="a11", description="가격 12,000원\r\n수량 30개 확인")],
            note="첫 줄\n둘째 줄",
        )
    )

    assert "1. 가격은 12,000원 수량은 30개\n" in page
    assert "1. 가격 12,000원 수량 30개 확인 — 담당 미지정 · 기한 없음\n" in page
    # The memo is a section of its own and keeps its lines.
    assert "메모\n첫 줄\n둘째 줄" in page


def test_lists_decisions_and_actions_as_the_review_has_them_and_the_memo() -> None:
    assert text(SUMMARY, title_of(SUMMARY)) == "\n".join(
        [
            "회의록 — 주간 회의",
            "",
            "결정 사항",
            "1. 배포는 다음 주 화요일에 한다",
            "2. 검색 개편은 2주 미룬다 (자동 추출)",
            "",
            "할 일",
            "1. 스펙 초안 공유 — 김민경 · 2026년 10월 9일 금 · 진행 중",
            "2. QA 일정 확인 — 민구 · 기한 없음 · 확인 필요",
            "3. 회고 자료 정리 — 담당 미지정 · 기한 없음 · 완료",
            "",
            "메모",
            "다음 회의는 목요일.",
        ]
    )


def test_is_headed_with_the_meetings_title_and_day_and_says_nothing_after_an_item_not_begun() -> (
    None
):
    page = text(
        with_(
            meeting_title="10월 2주차 점검",
            meeting_started_at=STARTED,
            decisions=[],
            action_items=[item(id="a5", assignee_name="박재경", status="todo")],
            note=None,
        ),
        "10월 2주차 점검",
    )

    assert page.split("\n")[:2] == ["회의록 — 10월 2주차 점검", "2026년 10월 8일 (목)"]
    assert "1. 스펙 초안 공유 — 박재경 · 기한 없음" in page
    assert "진행 전" not in page


def test_writes_a_due_date_as_the_date_line_writes_a_day_and_leaves_the_year_to_that_line() -> None:
    page = text(
        with_(
            meeting_started_at=STARTED,
            action_items=[
                item(id="a6", due_date=date(2026, 10, 13), status="todo"),
                item(id="a7", description="내년 예산안 내기", due_date=date(2027, 1, 5)),
            ],
        )
    )

    assert "2026년 10월 8일 (목)" in page
    assert "1. 스펙 초안 공유 — 담당 미지정 · 10월 13일 화\n" in page
    assert "2. 내년 예산안 내기 — 담당 미지정 · 2027년 1월 5일 화\n" in page
    assert "2026-10-13" not in page


def test_writes_a_decisions_deadline_as_it_writes_an_actions() -> None:
    page = text(
        with_(
            meeting_started_at=STARTED,
            decisions=[
                decision("dec_3", "배포는 미룹니다 (담당 박지영, 기한 2026-10-13)"),
                decision("dec_4", "예산안은 새해에 냅니다 (기한 2027-01-05)", "pending"),
                decision("dec_5", "2026-10-20에 다시 봅니다"),
            ],
            action_items=[item(id="a6", due_date=date(2026, 10, 13), status="todo")],
        )
    )

    assert "1. 배포는 미룹니다 (담당 박지영, 기한 10월 13일 화)\n" in page
    assert "2. 예산안은 새해에 냅니다 (기한 2027년 1월 5일 화) (자동 추출)\n" in page
    # A date somebody said is theirs; only the deadline the server appended is rewritten.
    assert "3. 2026-10-20에 다시 봅니다\n" in page
    assert "2026-10-13" not in page


def test_says_a_decisions_year_where_the_minutes_have_no_date_line_to_say_it() -> None:
    page = text(with_(decisions=[decision("dec_3", "배포는 미룹니다 (기한 2026-10-13)")]))

    assert "1. 배포는 미룹니다 (기한 2026년 10월 13일 화)\n" in page


def test_puts_a_models_summary_on_top_under_a_heading_that_says_a_model_wrote_it() -> None:
    page = text(
        with_(
            generated=GeneratedSummary(
                overview="배포를 다음 주로 미루기로 했습니다.",
                points=["QA 일정은 민구가 확인합니다"],
                model_version="llm:first",
                created_at=datetime(2026, 10, 8, 3, 30, tzinfo=UTC),
            )
        )
    )

    assert page.split("\n")[:5] == [
        "회의록",
        "",
        "요약 (AI 작성)",
        "배포를 다음 주로 미루기로 했습니다.",
        "- QA 일정은 민구가 확인합니다",
    ]


def test_names_the_meeting_by_its_own_title_before_a_rows() -> None:
    assert title_of(with_(meeting_title="10월 2주차 점검")) == "10월 2주차 점검"
    assert title_of(SUMMARY) == "주간 회의"


def test_carries_no_utterance() -> None:
    page = text(SUMMARY, title_of(SUMMARY))

    assert "utt_1" not in page


def test_leaves_a_candidate_out() -> None:
    page = minutes_page(SUMMARY, None)

    assert "모델이 확신하지 못한 것" not in page.text
    assert page.candidates == 1 and "a4" not in page.action_ids


def test_says_so_when_the_meeting_settled_nothing() -> None:
    empty = with_(decisions=[], action_items=[], note=None)

    assert text(empty) == "\n".join(["회의록", "", "결정 사항", "없음", "", "할 일", "없음"])
    assert title_of(empty) is None


# --- only here: the page leaves B, so what it carries is screened ---------------------


def test_a_line_carrying_personal_data_is_left_off_counted_and_the_rest_renumbered() -> None:
    page = minutes_page(
        with_(
            decisions=[
                decision("dec_1", "고객 010-1234-5678에게 회신한다"),
                decision("dec_2", "배포는 다음 주 화요일에 한다"),
            ],
            action_items=[
                item(id="a1", description="계좌 110-123-456789로 환불"),
                item(id="a2", description="QA 일정 확인"),
            ],
            note="연락처 010-9876-5432",
        ),
        "주간 회의",
    )

    assert "010-1234-5678" not in page.text and "110-123-456789" not in page.text
    assert "010-9876-5432" not in page.text and "메모" not in page.text
    assert "1. 배포는 다음 주 화요일에 한다" in page.text
    assert "1. QA 일정 확인 — 담당 미지정 · 기한 없음" in page.text
    assert page.held_back == 3
    assert page.decision_ids == ["dec_2"] and page.action_ids == ["a2"]


def test_a_title_or_overview_carrying_personal_data_is_left_off() -> None:
    page = minutes_page(
        with_(
            decisions=[],
            action_items=[],
            note=None,
            generated=GeneratedSummary(
                overview="회의를 했습니다.",
                points=["담당자 번호 010-1234-5678"],
                model_version="llm:first",
                created_at=datetime(2026, 10, 8, 3, 30, tzinfo=UTC),
            ),
        ),
        "010-2222-3333 고객 미팅",
    )

    assert page.text == "\n".join(["회의록", "", "결정 사항", "없음", "", "할 일", "없음"])
    assert page.held_back == 2
