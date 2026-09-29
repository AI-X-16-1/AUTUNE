"""The pre-meeting brief's wording and shape -- ``notify.build_pre_meeting_brief``."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from autune_context.briefs import minutes_until
from autune_context.notify import (
    BRIEF_ITEM_CHARS,
    BRIEF_URL_CHARS,
    MAX_BRIEF_AGENDA,
    MAX_BRIEF_DECISIONS,
    MAX_BRIEF_TOPICS,
    AgendaItem,
    BriefDecision,
    BriefRecap,
    build_pre_meeting_brief,
)
from autune_contracts import ChangeType
from autune_integrations.privacy import check_outbound

# 06:00 UTC is 15:00 in Korea.
_STARTS_AT = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)


def _text(blocks: list[dict]) -> str:
    parts = []
    for block in blocks:
        if "text" in block:
            parts.append(block["text"]["text"])
        for element in block.get("elements", []):
            parts.append(element["text"])
    return "\n".join(parts)


def _recap(**overrides: object) -> BriefRecap:
    fields: dict[str, object] = {
        "meeting_id": "mtg_prev",
        "title": "주간 회의",
        "day": date(2026, 9, 23),
        "topics": ("검색 정렬", "결제 모듈"),
        "decisions": (
            BriefDecision("검색 결과는 최신순으로 정렬한다", ChangeType.NEW),
            BriefDecision("결제 모듈 출시는 10월로 미룬다", ChangeType.REVERSED),
            BriefDecision("캐시 만료는 1시간으로 한다", ChangeType.MODIFIED),
        ),
    }
    fields.update(overrides)
    return BriefRecap(**fields)  # type: ignore[arg-type]


def _build(**overrides: object) -> tuple[str, list[dict]]:
    fields: dict[str, object] = {
        "title": "주간 회의",
        "starts_at": _STARTS_AT,
        "minutes_until": 10,
        "recap": _recap(),
        "recap_gone": False,
        "agenda": (),
    }
    fields.update(overrides)
    return build_pre_meeting_brief(**fields)  # type: ignore[arg-type]


def test_brief_says_when_the_meeting_starts_in_korean_time() -> None:
    fallback, blocks = _build()

    assert fallback == "10분 뒤 회의: 주간 회의"
    assert "15:00 시작" in _text(blocks)


def test_recap_names_the_past_meeting_its_topics_and_decisions() -> None:
    _, blocks = _build()
    text = _text(blocks)

    assert "2026년 9월 23일 「주간 회의」" in text
    assert "• 검색 정렬" in text
    assert "• 검색 결과는 최신순으로 정렬한다\n" in text  # a new decision carries no tag
    assert "• 결제 모듈 출시는 10월로 미룬다 (번복)" in text
    assert "• 캐시 만료는 1시간으로 한다 (변경)" in text


def test_recap_without_a_date_omits_it_rather_than_guessing() -> None:
    _, blocks = _build(recap=_recap(day=None))

    assert "*지난 회의* — 「주간 회의」" in _text(blocks)


def test_recap_says_so_when_the_past_meeting_recorded_no_decision() -> None:
    _, blocks = _build(recap=_recap(decisions=()))

    assert "지난 회의에서 기록된 결정이 없습니다." in _text(blocks)


def test_an_unrelated_latest_meeting_is_named_but_not_quoted() -> None:
    _, blocks = _build(recap_is_related=False)
    text = _text(blocks)

    assert "*팀의 최근 회의* — 2026년 9월 23일 「주간 회의」" in text
    assert "이어지는 지난 회의를 찾지 못해" in text
    assert "검색 정렬" not in text
    assert "결제 모듈 출시는 10월로 미룬다" not in text


def test_no_past_meeting_and_a_deleted_one_read_differently() -> None:
    _, none_blocks = _build(recap=None, recap_gone=False)
    _, gone_blocks = _build(recap=None, recap_gone=True)

    assert "참고할 지난 회의가 없습니다." in _text(none_blocks)
    assert "보존 기간이 지나 삭제되었습니다." in _text(gone_blocks)


def test_agenda_lists_issues_with_key_link_and_status() -> None:
    _, blocks = _build(
        agenda=(
            AgendaItem(
                title="검색 정렬 버그", key="AUT-12", status="진행 중", url="https://j/AUT-12"
            ),
            AgendaItem(title="결제 모듈 QA", key="AUT-13"),
            AgendaItem(title="키 없는 안건"),
        )
    )
    text = _text(blocks)

    assert "• <https://j/AUT-12|AUT-12> 검색 정렬 버그 (진행 중)" in text
    assert "• AUT-13 결제 모듈 QA" in text
    assert "• 키 없는 안건" in text


def test_an_overlong_link_falls_back_to_the_bare_key() -> None:
    url = "https://j/" + "x" * BRIEF_URL_CHARS
    _, blocks = _build(agenda=(AgendaItem(title="검색 정렬 버그", key="AUT-12", url=url),))

    assert "• AUT-12 검색 정렬 버그" in _text(blocks)
    assert url not in _text(blocks)


def test_no_agenda_is_stated_not_left_blank() -> None:
    _, blocks = _build(agenda=())

    assert "이번 회의에 연결된 안건이 없습니다." in _text(blocks)


def test_lists_past_their_cap_collapse_into_a_count() -> None:
    topics = tuple(f"주제{i}" for i in range(MAX_BRIEF_TOPICS + 3))
    _, blocks = _build(recap=_recap(topics=topics))
    text = _text(blocks)

    assert f"주제{MAX_BRIEF_TOPICS - 1}" in text
    assert f"주제{MAX_BRIEF_TOPICS}" not in text
    assert "외 3건" in text


def test_the_largest_brief_the_caps_allow_passes_the_outbound_guard() -> None:
    # The guard refuses an oversized post outright; the caps are what keep a
    # brief under it, whatever lengths the rows come in.
    long = "가" * (BRIEF_ITEM_CHARS * 3)
    recap = _recap(
        title=long,
        topics=tuple(f"{i}{long}" for i in range(MAX_BRIEF_TOPICS + 5)),
        decisions=tuple(
            BriefDecision(f"{i}{long}", ChangeType.REVERSED) for i in range(MAX_BRIEF_DECISIONS + 5)
        ),
    )
    agenda = tuple(
        AgendaItem(
            title=f"{i}{long}",
            key=f"{i}{long}",
            status=f"{i}{long}",
            url=f"https://j/{i}".ljust(BRIEF_URL_CHARS, "x"),
        )
        for i in range(MAX_BRIEF_AGENDA + 5)
    )
    fallback, blocks = _build(title=long, recap=recap, agenda=agenda)

    check_outbound({"channel": "C0", "text": fallback, "blocks": blocks}, destination="slack")


def test_minutes_until_rounds_up_and_never_says_zero() -> None:
    assert minutes_until(_STARTS_AT, _STARTS_AT - timedelta(minutes=10)) == 10
    assert minutes_until(_STARTS_AT, _STARTS_AT - timedelta(minutes=9, seconds=1)) == 10
    assert minutes_until(_STARTS_AT, _STARTS_AT - timedelta(seconds=5)) == 1
