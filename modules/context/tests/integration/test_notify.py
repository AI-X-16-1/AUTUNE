"""Integration tests for ``service.notify_topic_links`` and
``service.notify_decision_drift`` against a real PostgreSQL.

Rows are built directly (as ``test_read_api.py`` does for its topic-filter
tests) rather than through ``run_topic_linking``/``build_decision_lineage`` --
these tests are about what gets sent once the rows exist, not about how they
get built.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from autune_context import service
from autune_context.constants import EMBEDDING_DIM
from autune_context.models import CtxDecision, CtxDecisionVersion, CtxEmbedding, CtxTopicLink
from autune_core import Meeting, Participant, Team, TeamMember, User, Utterance, session_scope
from autune_core.ids import new_id
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


def _member(team_id: str, *, left: bool = False) -> str:
    """A user on ``team_id`` -- or, with ``left``, one who was and is not now.

    A drift DM goes only to somebody who is on the team when it is collected,
    so a recipient here has to be a real ``TeamMember``."""
    with session_scope() as s:
        user = User(email=f"{new_id('usr')}@notify.test", display_name="알림 테스트")
        s.add(user)
        s.flush()
        if not left:
            s.add(TeamMember(team_id=team_id, user_id=user.id))
        return user.id


def _meeting(team_id: str, *, days_ago: int = 0, started_at: datetime | None = None) -> str:
    with session_scope() as s:
        row = Meeting(
            team_id=team_id,
            title="회의",
            status="analyzing",
            started_at=started_at or datetime.now(tz=UTC) - timedelta(days=days_ago),
        )
        s.add(row)
        s.flush()
        return row.id


def _spoken(s: Session, meeting_id: str, label: str, *, consented: bool = True) -> None:
    """The segment ``label`` was cut from, stored as topic linking stores it, so a
    link carrying that label reads as cut from a consenting speaker's speech."""
    speaker = Participant(meeting_id=meeting_id, speaker_label=new_id("spk"), consented=consented)
    s.add(speaker)
    s.flush()
    utterance = Utterance(
        meeting_id=meeting_id,
        participant_id=speaker.id,
        speaker_label=speaker.speaker_label,
        start_sec=0.0,
        end_sec=1.0,
        text=label,
    )
    s.add(utterance)
    s.flush()
    s.add(
        CtxEmbedding(
            meeting_id=meeting_id,
            kind="topic",
            ref_label=label,
            utterance_ids=[utterance.id],
            embedding=[1.0] + [0.0] * (EMBEDDING_DIM - 1),
            model_version="test",
        )
    )


def _topic_link(
    meeting_id: str,
    *,
    status: str,
    linked_meeting_id: str | None = None,
    linked_meeting_date: date | None = None,
    topic_label: str = "검색 정렬",
) -> int:
    with session_scope() as s:
        _spoken(s, meeting_id, topic_label)
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
    current_statement: str = "최신순으로 정렬한다",
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
                current_statement=current_statement,
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
    today = date.today()
    _topic_link(meeting, status="asserted", linked_meeting_id=past, linked_meeting_date=today)
    _topic_link(meeting, status="pending", linked_meeting_id=past, linked_meeting_date=today)
    _topic_link(meeting, status="confirmed", linked_meeting_id=past, linked_meeting_date=today)
    _topic_link(meeting, status="rejected", linked_meeting_id=past, linked_meeting_date=today)
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
        linked_meeting_date=date.today(),
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
    alice, bob = _member(team_id), _member(team_id)
    _decision_version(
        team_id, meeting, change_type="modified", key_stakeholders_absent=[alice, bob]
    )
    slack = FakeSlack()

    with session_scope() as s:
        sent = service.notify_decision_drift(s, slack, _CHANNEL, meeting)

    assert sent == 1
    assert len(slack.channel_messages) == 1
    dms = [m for m in slack.sent if m.is_dm]
    assert {m.channel for m in dms} == {alice, bob}


def test_somebody_who_left_the_team_since_the_list_was_made_is_not_told(team_id: str) -> None:
    """``key_stakeholders_absent`` is stored when the lineage is built and read
    here, later. A person who left in between is no longer on the team whose
    bot would send, and the DM quotes the decision's statement."""
    meeting = _meeting(team_id)
    stayed, left = _member(team_id), _member(team_id, left=True)
    _decision_version(
        team_id, meeting, change_type="modified", key_stakeholders_absent=[left, stayed]
    )
    slack = FakeSlack()

    with session_scope() as s:
        sent = service.notify_decision_drift(s, slack, _CHANNEL, meeting)

    assert sent == 1
    assert [m.channel for m in slack.sent if m.is_dm] == [stayed]
    assert len(slack.channel_messages) == 1


def test_a_change_whose_absentees_have_all_left_sends_nothing_at_all(team_id: str) -> None:
    """The channel notice says how many were absent; with nobody left to tell it
    would announce an absence nobody can act on, as it never does for a change
    with no absent person."""
    meeting = _meeting(team_id)
    left = _member(team_id, left=True)
    _decision_version(team_id, meeting, change_type="reversed", key_stakeholders_absent=[left])
    slack = FakeSlack()

    with session_scope() as s:
        sent = service.notify_decision_drift(s, slack, _CHANNEL, meeting)

    assert sent == 0
    assert slack.sent == []


def test_an_absentee_of_another_team_is_not_told(team_id: str) -> None:
    """Being a member somewhere is not being on *this* team: the DM goes out
    through this team's bot, with this team's decision in it."""
    with session_scope() as s:
        other = Team(name="notify-test-other")
        s.add(other)
        s.flush()
        other_id = other.id
    try:
        outsider = _member(other_id)
        meeting = _meeting(team_id)
        _decision_version(
            team_id, meeting, change_type="modified", key_stakeholders_absent=[outsider]
        )
        slack = FakeSlack()

        with session_scope() as s:
            sent = service.notify_decision_drift(s, slack, _CHANNEL, meeting)

        assert sent == 0
        assert slack.sent == []
    finally:
        with session_scope() as s:
            s.execute(delete(Team).where(Team.id == other_id))


def test_a_new_decision_is_not_a_drift(team_id: str) -> None:
    meeting = _meeting(team_id)
    _decision_version(team_id, meeting, change_type="new", key_stakeholders_absent=[])
    slack = FakeSlack()

    with session_scope() as s:
        sent = service.notify_decision_drift(s, slack, _CHANNEL, meeting)

    assert sent == 0
    assert slack.sent == []


def test_an_unchanged_restatement_is_not_a_drift(team_id: str) -> None:
    """NLI entailment (the statement was re-affirmed, not moved) lands as
    ``unchanged``. Notifying on it would tell an absent stakeholder a decision
    changed when it did not."""
    meeting = _meeting(team_id)
    _decision_version(
        team_id, meeting, change_type="unchanged", key_stakeholders_absent=[_member(team_id)]
    )
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


def test_the_notice_dates_an_early_morning_meeting_by_the_korean_day(team_id: str) -> None:
    """08:00 KST on the 18th is 23:00 UTC on the 17th; the notice must say the 18th."""
    meeting = _meeting(team_id, started_at=datetime(2026, 9, 17, 23, 0, tzinfo=UTC))
    _decision_version(
        team_id, meeting, change_type="modified", key_stakeholders_absent=[_member(team_id)]
    )
    slack = FakeSlack()

    with session_scope() as s:
        service.notify_decision_drift(s, slack, _CHANNEL, meeting)

    assert "2026년 9월 18일" in slack.channel_messages[0].text


def test_the_channel_notice_names_no_one(team_id: str) -> None:
    meeting = _meeting(team_id)
    alice = _member(team_id)
    _decision_version(team_id, meeting, change_type="modified", key_stakeholders_absent=[alice])
    slack = FakeSlack()

    with session_scope() as s:
        service.notify_decision_drift(s, slack, _CHANNEL, meeting)

    assert alice not in slack.channel_messages[0].text


def test_an_unusually_long_statement_still_sends(team_id: str) -> None:
    """``current_statement`` is an unbounded ``Text`` column. Left untruncated
    it can push the outbound payload past ``assert_within_size``'s 4000-char
    cap and raise ``PrivacyViolationError`` -- and since the caller retries
    (``acks_late``), an untruncated statement would fail the same meeting's
    drift warning forever rather than just once."""
    meeting = _meeting(team_id)
    _decision_version(
        team_id,
        meeting,
        change_type="modified",
        current_statement="가" * 5000,
        key_stakeholders_absent=[_member(team_id)],
    )
    slack = FakeSlack()

    with session_scope() as s:
        sent = service.notify_decision_drift(s, slack, _CHANNEL, meeting)

    assert sent == 1
    assert len(slack.channel_messages) == 1


def test_the_dm_carries_no_raw_id_in_its_text(team_id: str) -> None:
    meeting = _meeting(team_id)
    alice = _member(team_id)
    _decision_version(team_id, meeting, change_type="modified", key_stakeholders_absent=[alice])
    slack = FakeSlack()

    with session_scope() as s:
        service.notify_decision_drift(s, slack, _CHANNEL, meeting)

    dm = next(m for m in slack.sent if m.is_dm)
    assert alice not in dm.text
