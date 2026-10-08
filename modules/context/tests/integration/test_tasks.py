"""``notify_context_events`` task behaviour: claiming, skipping, and the
"collect in session, send after" split. Against real PostgreSQL.

Mirrors modules/intelligence/tests/integration/test_tasks.py: monkeypatch
``tasks.session_scope`` to the test's transactional ``db_session`` so writes
roll back, and patch ``tasks.SlackClient`` so nothing real is sent.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from autune_context import briefs, tasks
from autune_context.constants import EMBEDDING_DIM
from autune_context.models import (
    CtxBrief,
    CtxDecision,
    CtxDecisionVersion,
    CtxEmbedding,
    CtxMeetingStatus,
    CtxTopicLink,
)
from autune_core import Meeting, Participant, TeamMember, User, Utterance
from autune_core.ids import new_id
from autune_integrations import TransientIntegrationError


@pytest.fixture
def use_test_session(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    @contextlib.contextmanager
    def _scope() -> Iterator[Session]:
        yield db_session

    monkeypatch.setattr(tasks, "session_scope", _scope)


@pytest.fixture
def fake_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real Fernet key, bypassing AUTUNE_ENCRYPTION_KEY for this test.

    ``autune_core.crypto._fernet()`` is itself lru_cached from settings, so a
    test running after any earlier test has already resolved settings cannot
    fix this by setting the environment variable -- patching the module
    attribute directly sidesteps that. Same fixture as
    modules/intelligence/tests/integration/test_tasks.py.
    """
    from cryptography.fernet import Fernet

    from autune_core import crypto

    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(crypto, "_fernet", lambda: key)


def _connect_slack(db_session: Session, team_id: str, config: dict) -> None:
    from autune_core import TeamIntegration
    from autune_core.crypto import encrypt

    db_session.add(
        TeamIntegration(
            team_id=team_id, service="slack", secret=encrypt("xoxb-test"), config=config
        )
    )
    db_session.flush()


def _member(db_session: Session, team_id: str) -> str:
    """A user on the team: a drift DM goes only to somebody who is on it when
    the notice is collected."""
    user = User(email=f"{new_id('usr')}@tasks.test", display_name="태스크 테스트")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team_id, user_id=user.id))
    db_session.flush()
    return user.id


def _status(db_session: Session, meeting_id: str) -> CtxMeetingStatus:
    row = CtxMeetingStatus(meeting_id=meeting_id, topic_linking_done=True, lineage_done=True)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.mark.usefixtures("use_test_session")
def test_skips_a_team_without_slack(db_session: Session, meeting: str, team: str) -> None:
    _status(db_session, meeting)

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.notify_context_events(meeting)

    slack_client_cls.assert_not_called()
    assert db_session.get(CtxMeetingStatus, meeting).notified_at is None


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_skips_a_team_without_a_configured_channel(
    db_session: Session, meeting: str, team: str
) -> None:
    _status(db_session, meeting)
    _connect_slack(db_session, team, config={})

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.notify_context_events(meeting)

    slack_client_cls.assert_not_called()
    assert db_session.get(CtxMeetingStatus, meeting).notified_at is None


@pytest.mark.usefixtures("use_test_session")
def test_a_meeting_that_no_longer_exists_is_skipped(db_session: Session) -> None:
    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.notify_context_events("meeting_gone")

    slack_client_cls.assert_not_called()


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_a_meeting_with_nothing_to_notify_still_claims(
    db_session: Session, meeting: str, team: str
) -> None:
    """No topic links, no drift -- the claim still happens, so a redelivered
    execution doesn't re-derive an (empty) send."""
    _status(db_session, meeting)
    _connect_slack(db_session, team, config={"channel": "C123"})

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.notify_context_events(meeting)

    slack_client_cls.return_value.post_message.assert_not_called()
    assert db_session.get(CtxMeetingStatus, meeting).notified_at is not None


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_sends_once_and_a_redelivered_execution_is_a_no_op(
    db_session: Session, meeting: str, team: str
) -> None:
    _status(db_session, meeting)
    _connect_slack(db_session, team, config={"channel": "C123"})
    thread = CtxDecision(team_id=team, topic_label="검색 정렬 기준")
    db_session.add(thread)
    db_session.flush()
    db_session.add(
        CtxDecisionVersion(
            thread_id=thread.id,
            source_decision_id="dec_direct",
            meeting_id=meeting,
            current_statement="최신순으로 정렬한다",
            change_type="modified",
            confidence=0.9,
            nli_version="test",
            key_stakeholders_absent=[_member(db_session, team)],
        )
    )
    db_session.flush()

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.notify_context_events(meeting)  # first execution: sends
        tasks.notify_context_events(meeting)  # Celery redelivery: no-op

    slack_client_cls.assert_called_once()
    assert slack_client_cls.return_value.post_message.call_count == 1  # channel notice
    assert slack_client_cls.return_value.send_dm.call_count == 1  # one absent stakeholder


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


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_a_catch_up_owed_skips_drift_here_but_still_sends_topic_links(
    db_session: Session, meeting: str, team: str
) -> None:
    """``build_decision_lineage`` can commit ``late_drift_due_at`` and the
    ``ctx_decision_versions`` row it covers between this task's enqueue and
    its run -- see the docstring on ``late_drift_due_at`` in ``tasks.py``.
    Without checking it here too, this task would find that same drift via
    ``collect_drift_notices`` and send it, and ``notify_late_drift`` would
    send it again once it claims. Topic links are unaffected -- they are not
    part of that race.
    """
    status = _status(db_session, meeting)
    status.late_drift_due_at = datetime.now(tz=UTC)
    _connect_slack(db_session, team, config={"channel": "C123"})
    _spoken(db_session, meeting, "검색 정렬 기준")
    db_session.add(
        CtxTopicLink(
            meeting_id=meeting,
            topic_label="검색 정렬 기준",
            linked_meeting_date=date.today(),
            similarity=0.9,
            rerank_score=0.9,
            confidence=0.9,
            status="asserted",
            retriever_version="test",
            reranker_version="test",
        )
    )
    thread = CtxDecision(team_id=team, topic_label="검색 정렬 기준")
    db_session.add(thread)
    db_session.flush()
    db_session.add(
        CtxDecisionVersion(
            thread_id=thread.id,
            source_decision_id="dec_direct",
            meeting_id=meeting,
            current_statement="최신순으로 정렬한다",
            change_type="modified",
            confidence=0.9,
            nli_version="test",
            key_stakeholders_absent=[_member(db_session, team)],
        )
    )
    db_session.flush()

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.notify_context_events(meeting)

    assert slack_client_cls.return_value.post_message.call_count == 1  # topic link only
    slack_client_cls.return_value.send_dm.assert_not_called()  # no drift DM
    status = db_session.get(CtxMeetingStatus, meeting)
    assert status.notified_at is not None
    assert status.late_drift_due_at is not None  # notify_late_drift still owns clearing it


# --------------------------------------------------------------------------- #
# notify_late_drift — drift only, its own claim
#
# publish_if_ready(force=...)'s routing to notify_late_drift vs.
# notify_context_events is tested in test_decision_lineage.py instead --
# service.publish_if_ready opens its own session_scope() rather than taking a
# session argument, so it can't see this file's transactional db_session; it
# needs the real, committed rows test_decision_lineage.py's local fixtures
# already provide.
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_notify_late_drift_sends_only_drift_and_claims_separately_from_notified_at(
    db_session: Session, meeting: str, team: str
) -> None:
    status = CtxMeetingStatus(
        meeting_id=meeting,
        topic_linking_done=True,
        lineage_done=True,
        extraction_seen=True,
        published_at=datetime.now(tz=UTC),
        notified_at=datetime.now(tz=UTC),  # topic links already went out once
    )
    db_session.add(status)
    _connect_slack(db_session, team, config={"channel": "C123"})
    thread = CtxDecision(team_id=team, topic_label="검색 정렬 기준")
    db_session.add(thread)
    db_session.flush()
    db_session.add(
        CtxDecisionVersion(
            thread_id=thread.id,
            source_decision_id="dec_direct",
            meeting_id=meeting,
            current_statement="최신순으로 정렬한다",
            change_type="modified",
            confidence=0.9,
            nli_version="test",
            key_stakeholders_absent=[_member(db_session, team)],
        )
    )
    db_session.flush()

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.notify_late_drift(meeting)

    assert slack_client_cls.return_value.post_message.call_count == 1
    assert slack_client_cls.return_value.send_dm.call_count == 1
    assert db_session.get(CtxMeetingStatus, meeting).late_drift_notified_at is not None


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_notify_late_drift_does_not_tell_somebody_who_left_since_the_list_was_made(
    db_session: Session, meeting: str, team: str
) -> None:
    """The catch-up warning is the long window: its list was made when the
    lineage finished, and this runs after B's timeout fallback. Whoever left the
    team in between gets no DM, and the claim still commits, so a redelivery does
    not look for somebody else to tell."""
    db_session.add(
        CtxMeetingStatus(
            meeting_id=meeting,
            topic_linking_done=True,
            lineage_done=True,
            extraction_seen=True,
            published_at=datetime.now(tz=UTC),
            notified_at=datetime.now(tz=UTC),
            late_drift_due_at=datetime.now(tz=UTC),
        )
    )
    _connect_slack(db_session, team, config={"channel": "C123"})
    thread = CtxDecision(team_id=team, topic_label="검색 정렬 기준")
    db_session.add(thread)
    db_session.flush()
    leaver = _member(db_session, team)
    db_session.add(
        CtxDecisionVersion(
            thread_id=thread.id,
            source_decision_id="dec_direct",
            meeting_id=meeting,
            current_statement="최신순으로 정렬한다",
            change_type="modified",
            confidence=0.9,
            nli_version="test",
            key_stakeholders_absent=[leaver],
        )
    )
    db_session.query(TeamMember).filter_by(team_id=team, user_id=leaver).delete()
    db_session.flush()

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.notify_late_drift(meeting)

    slack_client_cls.return_value.send_dm.assert_not_called()
    slack_client_cls.return_value.post_message.assert_not_called()
    status = db_session.get(CtxMeetingStatus, meeting)
    assert status.late_drift_notified_at is not None
    assert status.late_drift_due_at is None


@pytest.mark.usefixtures("use_test_session")
def test_notify_late_drift_for_a_team_without_slack_clears_what_is_owed(
    db_session: Session, meeting: str
) -> None:
    """Nothing to send to means nothing is owed. Left set, every later B reprocess
    would read "still owed" and force a republish to E, and a team that connects
    Slack later would get a notice for a meeting long past."""
    db_session.add(
        CtxMeetingStatus(
            meeting_id=meeting,
            topic_linking_done=True,
            extraction_seen=True,
            published_at=datetime.now(tz=UTC),
            late_drift_due_at=datetime.now(tz=UTC),
        )
    )
    db_session.flush()

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.notify_late_drift(meeting)

    slack_client_cls.assert_not_called()
    status = db_session.get(CtxMeetingStatus, meeting)
    assert status.late_drift_due_at is None
    assert status.late_drift_notified_at is None


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_notify_late_drift_redelivery_is_a_no_op(
    db_session: Session, meeting: str, team: str
) -> None:
    db_session.add(
        CtxMeetingStatus(
            meeting_id=meeting,
            topic_linking_done=True,
            late_drift_notified_at=datetime.now(tz=UTC),
        )
    )
    _connect_slack(db_session, team, config={"channel": "C123"})
    db_session.flush()

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.notify_late_drift(meeting)

    slack_client_cls.assert_not_called()


# --------------------------------------------------------------------------- #
# A transient Slack failure hands the claim back (#339)
# --------------------------------------------------------------------------- #


def _one_absent_drift(db_session: Session, meeting: str, team: str) -> None:
    thread = CtxDecision(team_id=team, topic_label="검색 정렬 기준")
    db_session.add(thread)
    db_session.flush()
    db_session.add(
        CtxDecisionVersion(
            thread_id=thread.id,
            source_decision_id="dec_direct",
            meeting_id=meeting,
            current_statement="최신순으로 정렬한다",
            change_type="modified",
            confidence=0.9,
            nli_version="test",
            key_stakeholders_absent=[_member(db_session, team)],
        )
    )
    db_session.flush()


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_a_rate_limit_while_sending_releases_the_claim_so_the_retry_sends(
    db_session: Session, meeting: str, team: str
) -> None:
    """Claimed first and failed halfway, the meeting used to stay claimed with
    its absent stakeholder untold: the retry found ``notified_at`` set and did
    nothing."""
    _status(db_session, meeting)
    _connect_slack(db_session, team, config={"channel": "C123"})
    _one_absent_drift(db_session, meeting, team)

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        slack_client_cls.return_value.post_message.side_effect = TransientIntegrationError(
            "rate limited"
        )
        with pytest.raises(TransientIntegrationError):
            tasks.notify_context_events(meeting)
        assert db_session.get(CtxMeetingStatus, meeting).notified_at is None

        slack_client_cls.return_value.post_message.side_effect = None
        tasks.notify_context_events(meeting)  # the retry

    assert slack_client_cls.return_value.send_dm.call_count == 1
    assert db_session.get(CtxMeetingStatus, meeting).notified_at is not None


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_a_failure_that_is_not_transient_keeps_the_claim(
    db_session: Session, meeting: str, team: str
) -> None:
    """A guard refusing the message would refuse it again; releasing the claim
    would only make the next run fail the same way."""
    _status(db_session, meeting)
    _connect_slack(db_session, team, config={"channel": "C123"})
    _one_absent_drift(db_session, meeting, team)

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        slack_client_cls.return_value.post_message.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            tasks.notify_context_events(meeting)

    assert db_session.get(CtxMeetingStatus, meeting).notified_at is not None


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_a_rate_limit_in_a_late_drift_restores_what_was_owed(
    db_session: Session, meeting: str, team: str
) -> None:
    """``late_drift_due_at`` goes back with the claim: ``notify_context_events``
    reads it to leave drift to this task."""
    owed_since = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
    db_session.add(
        CtxMeetingStatus(
            meeting_id=meeting,
            topic_linking_done=True,
            lineage_done=True,
            extraction_seen=True,
            published_at=datetime.now(tz=UTC),
            notified_at=datetime.now(tz=UTC),
            late_drift_due_at=owed_since,
        )
    )
    _connect_slack(db_session, team, config={"channel": "C123"})
    _one_absent_drift(db_session, meeting, team)

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        slack_client_cls.return_value.post_message.side_effect = TransientIntegrationError(
            "rate limited"
        )
        with pytest.raises(TransientIntegrationError):
            tasks.notify_late_drift(meeting)

        status = db_session.get(CtxMeetingStatus, meeting)
        assert status.late_drift_notified_at is None
        assert status.late_drift_due_at == owed_since

        slack_client_cls.return_value.post_message.side_effect = None
        tasks.notify_late_drift(meeting)  # the retry

    assert slack_client_cls.return_value.send_dm.call_count == 1
    status = db_session.get(CtxMeetingStatus, meeting)
    assert status.late_drift_notified_at is not None
    assert status.late_drift_due_at is None


# --------------------------------------------------------------------------- #
# A transient Slack failure gives the brief back to the clock (#339)
# --------------------------------------------------------------------------- #


def _scheduled_in(db_session: Session, meeting_id: str, *, minutes: int) -> None:
    row = db_session.get(Meeting, meeting_id)
    row.status = "scheduled"
    row.started_at = datetime.now(tz=UTC) + timedelta(minutes=minutes)
    db_session.flush()


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_a_rate_limit_posting_a_brief_leaves_it_due_for_the_next_tick(
    db_session: Session, meeting: str, team: str
) -> None:
    """The ``ctx_briefs`` row is the claim and ``due_meeting_starts`` skips a
    meeting that has one, so a brief that failed to post used to be lost."""
    _connect_slack(db_session, team, config={"channel": "C123"})
    _scheduled_in(db_session, meeting, minutes=5)

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        slack_client_cls.return_value.post_message.side_effect = TransientIntegrationError(
            "rate limited"
        )
        with pytest.raises(TransientIntegrationError):
            tasks.send_brief(meeting)

        assert db_session.get(CtxBrief, meeting) is None
        now = datetime.now(tz=UTC)
        assert meeting in {m for m, _ in briefs.due_meeting_starts(db_session, now)}

        slack_client_cls.return_value.post_message.side_effect = None
        tasks.send_brief(meeting)  # the next tick

    assert slack_client_cls.return_value.post_message.call_count == 2
    sent = db_session.get(CtxBrief, meeting)
    assert sent is not None and sent.sent_at is not None


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_a_brief_that_failed_for_another_reason_keeps_its_claim(
    db_session: Session, meeting: str, team: str
) -> None:
    _connect_slack(db_session, team, config={"channel": "C123"})
    _scheduled_in(db_session, meeting, minutes=5)

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        slack_client_cls.return_value.post_message.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            tasks.send_brief(meeting)

    assert db_session.get(CtxBrief, meeting) is not None
