"""``notify_context_events`` task behaviour: claiming, skipping, and the
"collect in session, send after" split. Against real PostgreSQL.

Mirrors modules/intelligence/tests/integration/test_tasks.py: monkeypatch
``tasks.session_scope`` to the test's transactional ``db_session`` so writes
roll back, and patch ``tasks.SlackClient`` so nothing real is sent.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from autune_context import tasks
from autune_context.models import CtxDecision, CtxDecisionVersion, CtxMeetingStatus


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
