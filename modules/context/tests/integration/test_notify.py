"""Integration tests for ``service.notify_topic_links`` and
``service.notify_decision_drift`` against a real PostgreSQL.

Rows are built directly (as ``test_read_api.py`` does for its topic-filter
tests) rather than through ``run_topic_linking``/``build_decision_lineage`` --
these tests are about what gets sent once the rows exist, not about how they
get built.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from autune_context import service
from autune_context.models import CtxDecision, CtxDecisionVersion, CtxTopicLink
from autune_core import Meeting, Team, session_scope
from autune_integrations.fakes import FakeSlack

_CHANNEL = "C0TESTCHANNEL"


@pytest.fixture
def team_id(db_engine: object) -> Iterator[str]:  # db_engine ensures migrations ran
    with session_scope() as s:
        row = Team(name="notify-test")
        s.add(row)
        s.flush()
        tid = row.id
    yield tid
    with session_scope() as s:
        s.execute(delete(Team).where(Team.id == tid))


def _meeting(team_id: str, *, days_ago: int = 0) -> str:
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title="회의",
            status="analyzing",
            started_at=datetime.now(tz=UTC) - timedelta(days=days_ago),
        )
        s.add(row)
        s.flush()
        return row.id


def _topic_link(
    meeting_id: str,
    *,
    status: str,
    linked_meeting_id: str | None = None,
    linked_meeting_date: datetime | None = None,
    topic_label: str = "검색 정렬",
) -> int:
    with session_scope() as s:
        row = CtxTopicLink(
            meeting_id=meeting_id,
            topic_label=topic_label,
            linked_meeting_id=linked_meeting_id,
            linked_meeting_date=linked_meeting_date,
            similarity=0.7,
            rerank_score=0.7,
            confidence=0.8,
            status=status,
            retriever_version="test",
            reranker_version="test",
        )
        s.add(row)
        s.flush()
        return row.id


def _decision_version(
    team_id: str,
    meeting_id: str,
    *,
    topic_label: str = "검색 정렬 기준",
    change_type: str = "modified",
    key_stakeholders_absent: list[str] | None = None,
) -> None:
    with session_scope() as s:
        thread = CtxDecision(team_id=team_id, topic_label=topic_label)
        s.add(thread)
        s.flush()
        s.add(
            CtxDecisionVersion(
                thread_id=thread.id,
                source_decision_id="dec_direct",
                meeting_id=meeting_id,
                current_statement="최신순으로 정렬한다",
                change_type=change_type,
                confidence=0.9,
                nli_version="test",
                key_stakeholders_absent=key_stakeholders_absent or [],
            )
        )


# --------------------------------------------------------------------------- #
# notify_topic_links
# --------------------------------------------------------------------------- #


def test_notifies_only_asserted_links(team_id: str) -> None:
    past = _meeting(team_id, days_ago=7)
    meeting = _meeting(team_id)
    now = datetime.now(tz=UTC)
    _topic_link(meeting, status="asserted", linked_meeting_id=past, linked_meeting_date=now)
    _topic_link(meeting, status="pending", linked_meeting_id=past, linked_meeting_date=now)
    _topic_link(meeting, status="confirmed", linked_meeting_id=past, linked_meeting_date=now)
    _topic_link(meeting, status="rejected", linked_meeting_id=past, linked_meeting_date=now)
    slack = FakeSlack()

    with session_scope() as s:
        sent = service.notify_topic_links(s, slack, _CHANNEL, meeting)

    assert sent == 1
    assert len(slack.channel_messages) == 1
    assert slack.channel_messages[0].channel == _CHANNEL


def test_topic_link_notice_carries_the_topic_label(team_id: str) -> None:
    past = _meeting(team_id, days_ago=3)
    meeting = _meeting(team_id)
    _topic_link(
        meeting,
        status="asserted",
        linked_meeting_id=past,
        linked_meeting_date=datetime.now(tz=UTC),
        topic_label="배포 전략",
    )
    slack = FakeSlack()

    with session_scope() as s:
        service.notify_topic_links(s, slack, _CHANNEL, meeting)

    assert "배포 전략" in slack.channel_messages[0].text


def test_a_meeting_with_no_links_sends_nothing(team_id: str) -> None:
    meeting = _meeting(team_id)
    slack = FakeSlack()

    with session_scope() as s:
        sent = service.notify_topic_links(s, slack, _CHANNEL, meeting)

    assert sent == 0
    assert slack.sent == []


# --------------------------------------------------------------------------- #
# notify_decision_drift
# --------------------------------------------------------------------------- #


def test_drift_warning_posts_once_to_the_channel_and_dms_each_absentee(team_id: str) -> None:
    meeting = _meeting(team_id)
    _decision_version(
        team_id, meeting, change_type="modified", key_stakeholders_absent=["usr_alice", "usr_bob"]
    )
    slack = FakeSlack()

    with session_scope() as s:
        sent = service.notify_decision_drift(s, slack, _CHANNEL, meeting)

    assert sent == 1
    assert len(slack.channel_messages) == 1
    dms = [m for m in slack.sent if m.is_dm]
    assert {m.channel for m in dms} == {"usr_alice", "usr_bob"}


def test_a_new_decision_is_not_a_drift(team_id: str) -> None:
    meeting = _meeting(team_id)
    _decision_version(team_id, meeting, change_type="new", key_stakeholders_absent=[])
    slack = FakeSlack()

    with session_scope() as s:
        sent = service.notify_decision_drift(s, slack, _CHANNEL, meeting)

    assert sent == 0
    assert slack.sent == []


def test_a_change_with_no_one_absent_sends_nothing(team_id: str) -> None:
    meeting = _meeting(team_id)
    _decision_version(team_id, meeting, change_type="reversed", key_stakeholders_absent=[])
    slack = FakeSlack()

    with session_scope() as s:
        sent = service.notify_decision_drift(s, slack, _CHANNEL, meeting)

    assert sent == 0
    assert slack.sent == []


def test_the_channel_notice_names_no_one(team_id: str) -> None:
    meeting = _meeting(team_id)
    _decision_version(
        team_id, meeting, change_type="modified", key_stakeholders_absent=["usr_alice"]
    )
    slack = FakeSlack()

    with session_scope() as s:
        service.notify_decision_drift(s, slack, _CHANNEL, meeting)

    assert "usr_alice" not in slack.channel_messages[0].text


def test_the_dm_carries_no_raw_id_in_its_text(team_id: str) -> None:
    meeting = _meeting(team_id)
    _decision_version(
        team_id, meeting, change_type="modified", key_stakeholders_absent=["usr_alice"]
    )
    slack = FakeSlack()

    with session_scope() as s:
        service.notify_decision_drift(s, slack, _CHANNEL, meeting)

    dm = next(m for m in slack.sent if m.is_dm)
    assert "usr_alice" not in dm.text
