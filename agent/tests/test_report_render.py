"""The Report subagent's template (autune-lab wiki/report-subagent-design.md)."""

from __future__ import annotations

from typing import Any

from autune_agent.results import ToolResult
from autune_agent.subagents.report.render import BODY_MAX_CHARS, has_pending, render


def _r(summary: str, items: list[dict[str, Any]] | None = None, **kw: Any) -> ToolResult:
    return ToolResult(ok=True, summary=summary, items=items or [], **kw)


ACTIONS = _r(
    "확정된 액션아이템 2건, 확인 대기 1건.",
    [
        {"title": "결제 API 스펙 초안", "body": "백엔드 · 2026-10-02 · todo"},
        {"title": "결제 화면 시안", "body": "디자인 · 2026-10-04 · todo"},
    ],
)
REVIEW = _r(
    "결정 확인 대기 1건, 액션아이템 확인 대기 1건, 답 없는 약한 동의 0건.",
    [{"title": "결정 확인 대기", "score": 1.0}],
)
GAPS = _r(
    "열린 갭 2건, 그중 높음 1건.",
    [
        {
            "title": "리스크·예외 처리 — 충분히 다뤄지지 않았습니다",
            "body": "이 방향이 실패하면 무엇을 합니까?",
            "severity": "high",
        },
        {"title": "일정: 출시 일정", "body": "", "severity": "medium"},
    ],
)
"""C's ``gap.open_gaps`` as it returns (modules/gap/.../tools.py). The first
title is a real one from the 10/1 run: C's titles carry their own dash."""
LINKS = _r(
    "이어지는 회의 1건.",
    [{"title": "간편결제는 2차로 미룬다", "meeting_title": "간편결제 도입 검토", "date": "9/22"}],
)


def test_every_section_in_order() -> None:
    assert render(ACTIONS, REVIEW, GAPS, LINKS) == (
        "✅ 확정된 액션 아이템\n"
        "• 결제 API 스펙 초안 — 백엔드 · 2026-10-02 · todo\n"
        "• 결제 화면 시안 — 디자인 · 2026-10-04 · todo\n"
        "⏳ 결정 확인 대기 1건, 액션아이템 확인 대기 1건, 답 없는 약한 동의 0건.\n"
        "\n"
        "⚠️ 열린 갭 2건, 그중 높음 1건.\n"
        "• 리스크·예외 처리 — 충분히 다뤄지지 않았습니다\n"
        "  ↳ 이 방향이 실패하면 무엇을 합니까?\n"
        "• 일정: 출시 일정\n"
        "\n"
        "🔗 이어지는 회의: 간편결제 도입 검토 (9/22)"
    )


def test_a_linked_meeting_is_named_never_quoted() -> None:
    assert "간편결제는 2차로 미룬다" not in render(ACTIONS, REVIEW, GAPS, LINKS)


def test_a_link_without_a_meeting_title_is_dropped() -> None:
    links = _r("", [{"title": "과거 결정 문장"}])
    assert "🔗" not in render(ACTIONS, REVIEW, GAPS, links)


def test_missing_tools_drop_their_sections_instead_of_saying_none() -> None:
    body = render(ACTIONS, None, None, None)
    assert "⏳" not in body and "💬" not in body and "⚠️" not in body and "🔗" not in body
    assert "없음" not in body


def test_a_failed_tool_is_treated_as_missing() -> None:
    assert "⚠️" not in render(ACTIONS, REVIEW, ToolResult.failure("down"), LINKS)


def test_a_meeting_with_no_open_gap_says_so() -> None:
    """C answered and found nothing: that is a fact about the meeting, unlike a missing tool."""
    gaps = _r("이 회의에 열린 갭이 없습니다.", [])
    # Good news, so not under the warning sign (review of #568).
    assert render(ACTIONS, None, gaps, None).endswith("\n\n✅ 이 회의에 열린 갭이 없습니다.")


def test_a_low_gap_is_left_out_as_c_s_own_screen_hides_it() -> None:
    """S20 keeps LOW behind a toggle; a team-channel post should not lead with it."""
    gaps = _r(
        "열린 갭 2건, 그중 높음 0건.",
        [
            {"title": "일정: 출시 일정", "body": "", "severity": "medium"},
            {"title": "기타: 회식 장소", "body": "", "severity": "low"},
        ],
    )
    body = render(ACTIONS, None, gaps, None)
    assert "출시 일정" in body and "회식 장소" not in body


def test_zero_confirmed_items_is_one_line() -> None:
    assert render(_r("확정된 액션아이템 0건, 확인 대기 0건."), None, None, None) == (
        "✅ 확정된 액션 아이템 없음"
    )


def test_nothing_pending_drops_the_pending_line() -> None:
    assert "⏳" not in render(ACTIONS, _r("결정 확인 대기 0건.", []), None, None)
    assert has_pending(_r("결정 확인 대기 0건.", [])) is False
    assert has_pending(REVIEW) is True
    assert has_pending(None) is False


def test_a_truncated_list_says_there_is_more() -> None:
    actions = _r("확정된 액션아이템 7건.", ACTIONS.model_dump()["items"], truncated=True)
    assert "더 있어요 — 상세보기에서" in render(actions, None, None, None)


def test_over_budget_the_other_modules_go_before_the_confirmed_items() -> None:
    """The confirmed items are the report; C's and D's lines are context."""
    long_links = _r(
        "",
        [{"title": "x", "meeting_title": "T" * 400, "date": "9/22"} for _ in range(5)],
    )
    long_gaps = _r("💬" * 300, [{"title": "G" * 300, "body": "b"} for _ in range(5)])

    body = render(ACTIONS, REVIEW, long_gaps, long_links)

    assert len(body) <= BODY_MAX_CHARS
    assert "• 결제 API 스펙 초안" in body and "• 결제 화면 시안" in body
    # Whole lines only: no line is cut in the middle.
    assert all(line.endswith("(9/22)") for line in body.splitlines() if line.startswith("🔗"))


def test_over_budget_a_question_goes_before_its_own_title() -> None:
    """Gap lines drop from the end, so a "↳" line never stays without its gap (#591 review)."""
    long_gaps = _r(
        "열린 갭 5건, 그중 높음 5건.",
        [
            {
                "title": f"항목{n} — 충분히 다뤄지지 않았습니다",
                "body": "질" * 500,
                "severity": "high",
            }
            for n in range(5)
        ],
    )

    body = render(ACTIONS, REVIEW, long_gaps, None)

    assert len(body) <= BODY_MAX_CHARS
    lines = body.splitlines()
    assert sum(line.startswith("  ↳") for line in lines) < 5  # the budget did cut
    for index, line in enumerate(lines):
        if line.startswith("  ↳"):
            assert lines[index - 1].startswith("• 항목")


def test_the_body_stays_under_the_budget() -> None:
    long_items = [{"title": "가" * 600, "body": "나"} for _ in range(5)]
    body = render(_r("확정 5건.", long_items), REVIEW, GAPS, LINKS)
    assert len(body) <= BODY_MAX_CHARS
    assert "더 있어요 — 상세보기에서" in body
