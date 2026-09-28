"""``notify_context_events`` task behaviour: claiming, skipping, and the
"collect in session, send after" split. Against real PostgreSQL.

Mirrors modules/intelligence/tests/integration/test_tasks.py: monkeypatch
``tasks.session_scope`` to the test's transactional ``db_session`` so writes
roll back, and patch ``tasks.SlackClient`` so nothing real is sent.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from autune_context import tasks
from autune_context.models import CtxDecision, CtxDecisionVersion, CtxMeetingStatus, CtxTopicLink


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
            key_stakeholders_absent=["usr_alice"],
        )
    )
    db_session.flush()

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.notify_context_events(meeting)  # first execution: sends
        tasks.notify_context_events(meeting)  # Celery redelivery: no-op

    slack_client_cls.assert_called_once()
    assert slack_client_cls.return_value.post_message.call_count == 1  # channel notice
    assert slack_client_cls.return_value.send_dm.call_count == 1  # one absent stakeholder


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
            key_stakeholders_absent=["usr_alice"],
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
            key_stakeholders_absent=["usr_alice"],
        )
    )
    db_session.flush()

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.notify_late_drift(meeting)

    assert slack_client_cls.return_value.post_message.call_count == 1
    assert slack_client_cls.return_value.send_dm.call_count == 1
    assert db_session.get(CtxMeetingStatus, meeting).late_drift_notified_at is not None


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
