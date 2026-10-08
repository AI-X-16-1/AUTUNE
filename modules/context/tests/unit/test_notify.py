"""Slack notices: what they say, and how ``service.send_*`` sends them. Pure --
no database, a ``FakeSlack`` instead of a real client -- like intelligence's
test_feedback.py."""

from __future__ import annotations

import re
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
from autune_core.errors import PrivacyViolationError
from autune_integrations import PermanentIntegrationError, TransientIntegrationError
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


_PING = "결정: <!channel> 배포는 <https://evil.example|board> & 금요일"
_PING_ESCAPED = "결정: &lt;!channel&gt; 배포는 &lt;https://evil.example|board&gt; &amp; 금요일"


def _carries_no_control_sequence(fallback: str, blocks: list[dict]) -> None:
    """Slack reads ``<...>`` in the fallback as well as in the blocks."""
    for text in (fallback, _text(blocks)):
        assert "<!channel>" not in text
        assert "<https://evil.example" not in text
        assert "&lt;!channel&gt;" in text


def test_topic_link_notice_escapes_the_topic_label() -> None:
    fallback, blocks = build_topic_link_notice(
        topic_label="<!channel>", linked_meeting_date=date(2026, 9, 4)
    )

    _carries_no_control_sequence(fallback, blocks)


def test_drift_channel_notice_escapes_the_label_and_the_statement() -> None:
    fallback, blocks = build_decision_drift_channel_notice(
        thread_label="<!channel>",
        current_statement=_PING,
        change_type=ChangeType.REVERSED,
        meeting_date=None,
    )

    _carries_no_control_sequence(fallback, blocks)
    assert _PING_ESCAPED in _text(blocks)


def test_drift_personal_dm_escapes_the_label_and_the_statement() -> None:
    fallback, blocks = build_decision_drift_personal_dm(
        thread_label="<!channel>",
        current_statement=_PING,
        change_type=ChangeType.MODIFIED,
        meeting_date=None,
    )

    assert "<!channel>" not in _text(blocks)
    assert _PING_ESCAPED in _text(blocks)
    assert "<" not in fallback


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
        meeting_date=date(2026, 9, 4),
    )

    text = _text(blocks)
    assert "usr_" not in text


@pytest.mark.parametrize("change_type", [ChangeType.MODIFIED, ChangeType.REVERSED])
def test_drift_channel_notice_does_not_say_how_many_were_absent(change_type: ChangeType) -> None:
    """A count of one points at one person in a small team (#339)."""
    fallback, blocks = build_decision_drift_channel_notice(
        thread_label="검색 정렬 기준",
        current_statement="최신순으로 정렬한다",
        change_type=change_type,
        meeting_date=date(2026, 9, 4),
    )

    assert not re.search(r"\d+\s*명", fallback + _text(blocks))


def test_drift_channel_notice_reflects_reversed_vs_modified() -> None:
    _fallback, reversed_blocks = build_decision_drift_channel_notice(
        thread_label="t",
        current_statement="s",
        change_type=ChangeType.REVERSED,
        meeting_date=None,
    )
    _fallback, modified_blocks = build_decision_drift_channel_notice(
        thread_label="t",
        current_statement="s",
        change_type=ChangeType.MODIFIED,
        meeting_date=None,
    )

    assert "번복" in _text(reversed_blocks)
    assert "변경" in _text(modified_blocks)


def test_drift_channel_notice_states_the_changing_meetings_date() -> None:
    fallback, blocks = build_decision_drift_channel_notice(
        thread_label="검색 정렬 기준",
        current_statement="최신순으로 정렬한다",
        change_type=ChangeType.MODIFIED,
        meeting_date=date(2026, 9, 4),
    )

    assert "2026년 9월 4일" in fallback
    assert "2026년 9월 4일" in _text(blocks)


def test_drift_channel_notice_omits_the_date_when_the_meeting_has_none() -> None:
    fallback, blocks = build_decision_drift_channel_notice(
        thread_label="검색 정렬 기준",
        current_statement="최신순으로 정렬한다",
        change_type=ChangeType.MODIFIED,
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


# --------------------------------------------------------------------------- #
# One refusal does not cost the rest (#478)
#
# Every send runs after its claim commits, so whatever a loop fails to reach
# is never retried. #478 makes Slack's ok:false and an unlinked DM recipient
# raise PermanentIntegrationError; these pin that D skips that one send and
# carries on, while a transient failure and the privacy guard still raise.
# --------------------------------------------------------------------------- #


class _Refusing(FakeSlack):
    """FakeSlack that refuses some sends: ``dm`` by recipient id, ``post`` by
    position among channel posts (0-based)."""

    def __init__(
        self,
        *,
        dm: dict[str, Exception] | None = None,
        post: dict[int, Exception] | None = None,
    ) -> None:
        super().__init__()
        self.refuse_dm = dm or {}
        self.refuse_post = post or {}
        self.posts_tried = 0

    def post_message(self, channel: str, text: str, blocks: list[dict] | None = None) -> str:
        attempt = self.posts_tried
        self.posts_tried += 1
        if attempt in self.refuse_post:
            raise self.refuse_post[attempt]
        return super().post_message(channel, text, blocks)

    def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = None) -> str:
        if user_id in self.refuse_dm:
            raise self.refuse_dm[user_id]
        return super().send_dm(user_id, text, blocks)


def _drift(*absent: str, label: str = "결제 모듈 교체") -> service.DriftNotice:
    return service.DriftNotice(
        thread_label=label,
        statement_preview="결제 모듈 교체는 다음 분기로 미룹니다",
        change_type=ChangeType.REVERSED,
        absent_user_ids=absent,
        meeting_date=date(2026, 9, 28),
    )


def _dm_recipients(slack: FakeSlack) -> list[str]:
    return [m.channel for m in slack.sent if m.is_dm]


def test_an_unreachable_absentee_does_not_cost_the_others_their_dm() -> None:
    slack = _Refusing(dm={"user_b": PermanentIntegrationError("not linked for direct messages")})

    posted = service.send_decision_drift_notices(
        slack, _CHANNEL, [_drift("user_a", "user_b", "user_c")]
    )

    assert posted == 1
    assert _dm_recipients(slack) == ["user_a", "user_c"]


def test_a_refused_channel_notice_still_sends_that_events_dms_and_the_next_event() -> None:
    """``not_in_channel`` once the bot is removed: the absentees are still told,
    and the next drift event is still tried."""
    slack = _Refusing(post={0: PermanentIntegrationError("slack refused: not_in_channel")})

    posted = service.send_decision_drift_notices(
        slack, _CHANNEL, [_drift("user_a", label="첫째"), _drift("user_b", label="둘째")]
    )

    assert posted == 1
    assert [m.text for m in slack.channel_messages if "둘째" in m.text]
    assert _dm_recipients(slack) == ["user_a", "user_b"]


def test_a_refused_topic_link_notice_skips_only_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    _capped_settings(monkeypatch, cap=3)
    slack = _Refusing(post={0: PermanentIntegrationError("slack refused: not_in_channel")})

    sent = service.send_topic_link_notices(slack, _CHANNEL, [_link("a"), _link("b"), _link("c")])

    assert sent == 2
    assert len(slack.channel_messages) == 2


def test_a_refused_rollup_is_not_counted_as_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    _capped_settings(monkeypatch, cap=1)
    slack = _Refusing(post={1: PermanentIntegrationError("slack refused: not_in_channel")})

    sent = service.send_topic_link_notices(slack, _CHANNEL, [_link("a"), _link("b"), _link("c")])

    assert sent == 1


def test_a_transient_failure_still_raises() -> None:
    """A rate limit or an outage is not silently turned into a lost notice."""
    slack = _Refusing(dm={"user_a": TransientIntegrationError("slack is down")})

    with pytest.raises(TransientIntegrationError):
        service.send_decision_drift_notices(slack, _CHANNEL, [_drift("user_a")])


def test_the_privacy_guard_is_not_swallowed() -> None:
    """``PrivacyViolationError`` is not a ``PermanentIntegrationError``; an
    unmasked statement must stop the send, not be skipped as unreachable."""
    assert not issubclass(PrivacyViolationError, PermanentIntegrationError)
    slack = FakeSlack()
    leaking = service.DriftNotice(
        thread_label="연락처",
        statement_preview="담당자 번호는 010-1234-5678입니다",
        change_type=ChangeType.MODIFIED,
        absent_user_ids=("user_a",),
        meeting_date=None,
    )

    with pytest.raises(PrivacyViolationError):
        service.send_decision_drift_notices(slack, _CHANNEL, [leaking])
