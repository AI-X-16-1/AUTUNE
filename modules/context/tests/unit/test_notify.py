"""Slack notices: what they say. Pure, like intelligence's test_feedback.py."""

from __future__ import annotations

from datetime import date

from autune_context.notify import (
    build_decision_drift_channel_notice,
    build_decision_drift_personal_dm,
    build_topic_link_notice,
)
from autune_contracts import ChangeType


def _text(blocks: list[dict]) -> str:
    """All rendered text in the blocks, flattened."""
    out: list[str] = []
    for block in blocks:
        section = block.get("text")
        if isinstance(section, dict):
            out.append(section.get("text", ""))
        for element in block.get("elements", []):
            out.append(element.get("text", ""))
    return "\n".join(out)


def test_topic_link_notice_states_the_korean_date() -> None:
    fallback, blocks = build_topic_link_notice(
        topic_label="검색 정렬", linked_meeting_date=date(2026, 9, 4)
    )

    assert "2026년 9월 4일" in fallback
    assert "2026년 9월 4일" in _text(blocks)


def test_topic_link_notice_carries_the_topic_label() -> None:
    _fallback, blocks = build_topic_link_notice(
        topic_label="검색 정렬 기준", linked_meeting_date=date(2026, 9, 4)
    )

    assert "검색 정렬 기준" in _text(blocks)


def test_drift_channel_notice_names_no_one() -> None:
    _fallback, blocks = build_decision_drift_channel_notice(
        thread_label="검색 정렬 기준",
        current_statement="최신순으로 정렬한다",
        change_type=ChangeType.REVERSED,
        absent_count=2,
    )

    text = _text(blocks)
    assert "usr_" not in text
    assert "2명" in text


def test_drift_channel_notice_reflects_reversed_vs_modified() -> None:
    _fallback, reversed_blocks = build_decision_drift_channel_notice(
        thread_label="t",
        current_statement="s",
        change_type=ChangeType.REVERSED,
        absent_count=1,
    )
    _fallback, modified_blocks = build_decision_drift_channel_notice(
        thread_label="t", current_statement="s", change_type=ChangeType.MODIFIED, absent_count=1
    )

    assert "번복" in _text(reversed_blocks)
    assert "변경" in _text(modified_blocks)


def test_drift_personal_dm_carries_no_id_or_name() -> None:
    fallback, blocks = build_decision_drift_personal_dm(
        thread_label="검색 정렬 기준",
        current_statement="최신순으로 정렬한다",
        change_type=ChangeType.MODIFIED,
    )

    assert "usr_" not in fallback
    assert "usr_" not in _text(blocks)


def test_drift_personal_dm_states_the_decision_content() -> None:
    _fallback, blocks = build_decision_drift_personal_dm(
        thread_label="검색 정렬 기준",
        current_statement="최신순으로 정렬한다",
        change_type=ChangeType.MODIFIED,
    )

    assert "최신순으로 정렬한다" in _text(blocks)
