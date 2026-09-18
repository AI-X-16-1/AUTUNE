"""Slack notices: what they say, and how ``service.send_*`` sends them. Pure --
no database, a ``FakeSlack`` instead of a real client -- like intelligence's
test_feedback.py."""

from __future__ import annotations

from datetime import date

import pytest

from autune_context import service
from autune_context.config import ContextSettings
from autune_context.notify import (
    build_decision_drift_channel_notice,
    build_decision_drift_personal_dm,
    build_topic_link_notice,
    build_topic_link_rollup_notice,
)
from autune_contracts import ChangeType
from autune_integrations.fakes import FakeSlack

_CHANNEL = "C0TESTCHANNEL"


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
        meeting_date=date(2026, 9, 4),
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
        meeting_date=None,
    )
    _fallback, modified_blocks = build_decision_drift_channel_notice(
        thread_label="t",
        current_statement="s",
        change_type=ChangeType.MODIFIED,
        absent_count=1,
        meeting_date=None,
    )

    assert "번복" in _text(reversed_blocks)
    assert "변경" in _text(modified_blocks)


def test_drift_channel_notice_states_the_changing_meetings_date() -> None:
    fallback, blocks = build_decision_drift_channel_notice(
        thread_label="검색 정렬 기준",
        current_statement="최신순으로 정렬한다",
        change_type=ChangeType.MODIFIED,
        absent_count=1,
        meeting_date=date(2026, 9, 4),
    )

    assert "2026년 9월 4일" in fallback
    assert "2026년 9월 4일" in _text(blocks)


def test_drift_channel_notice_omits_the_date_when_the_meeting_has_none() -> None:
    fallback, blocks = build_decision_drift_channel_notice(
        thread_label="검색 정렬 기준",
        current_statement="최신순으로 정렬한다",
        change_type=ChangeType.MODIFIED,
        absent_count=1,
        meeting_date=None,
    )

    assert "년" not in fallback
    assert "년" not in _text(blocks)


def test_drift_personal_dm_carries_no_id_or_name() -> None:
    fallback, blocks = build_decision_drift_personal_dm(
        thread_label="검색 정렬 기준",
        current_statement="최신순으로 정렬한다",
        change_type=ChangeType.MODIFIED,
        meeting_date=date(2026, 9, 4),
    )

    assert "usr_" not in fallback
    assert "usr_" not in _text(blocks)


def test_drift_personal_dm_states_the_decision_content() -> None:
    _fallback, blocks = build_decision_drift_personal_dm(
        thread_label="검색 정렬 기준",
        current_statement="최신순으로 정렬한다",
        change_type=ChangeType.MODIFIED,
        meeting_date=date(2026, 9, 4),
    )

    assert "최신순으로 정렬한다" in _text(blocks)


def test_drift_personal_dm_states_the_changing_meetings_date() -> None:
    fallback, _blocks = build_decision_drift_personal_dm(
        thread_label="검색 정렬 기준",
        current_statement="최신순으로 정렬한다",
        change_type=ChangeType.MODIFIED,
        meeting_date=date(2026, 9, 4),
    )

    assert "2026년 9월 4일" in fallback


def test_topic_link_rollup_notice_states_the_count() -> None:
    fallback, blocks = build_topic_link_rollup_notice(count=4)

    assert "4건" in fallback
    assert "4건" in _text(blocks)


# --------------------------------------------------------------------------- #
# service.send_topic_link_notices — the per-meeting message cap
# --------------------------------------------------------------------------- #


def _link(label: str) -> service.TopicLinkNotice:
    return service.TopicLinkNotice(topic_label=label, linked_meeting_date=date(2026, 9, 4))


def _capped_settings(monkeypatch: pytest.MonkeyPatch, cap: int) -> None:
    monkeypatch.setattr(
        service, "get_settings", lambda: ContextSettings(max_topic_link_notices=cap)
    )


def test_send_topic_link_notices_under_the_cap_sends_one_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _capped_settings(monkeypatch, cap=3)
    slack = FakeSlack()

    sent = service.send_topic_link_notices(slack, _CHANNEL, [_link("a"), _link("b")])

    assert sent == 2
    assert len(slack.channel_messages) == 2


def test_send_topic_link_notices_over_the_cap_rolls_up_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _capped_settings(monkeypatch, cap=2)
    slack = FakeSlack()

    sent = service.send_topic_link_notices(
        slack, _CHANNEL, [_link("a"), _link("b"), _link("c"), _link("d")]
    )

    assert sent == 4
    # 2 individual notices (the cap) + 1 rollup notice for the other 2.
    assert len(slack.channel_messages) == 3
    assert "2건" in slack.channel_messages[-1].text


def test_send_topic_link_notices_exactly_at_the_cap_has_no_rollup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _capped_settings(monkeypatch, cap=2)
    slack = FakeSlack()

    service.send_topic_link_notices(slack, _CHANNEL, [_link("a"), _link("b")])

    assert len(slack.channel_messages) == 2
