"""Module E completion handlers — enqueue behaviour, against real PostgreSQL.

The handlers open their own ``session_scope``. Each test points that at the
test's transactional ``db_session`` so writes roll back, and mocks ``aggregate``
so nothing is really queued.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from autune_contracts import ContextLinks, ExtractionResult, GapReport, IntelligenceSnapshot
from autune_intelligence import service, tasks
from autune_intelligence.models import IntelCompletion


@pytest.fixture
def use_test_session(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    @contextlib.contextmanager
    def _scope() -> Iterator[Session]:
        yield db_session

    monkeypatch.setattr(tasks, "session_scope", _scope)


@pytest.fixture
def mock_aggregate() -> Iterator[object]:
    with patch.object(tasks, "aggregate") as mock:
        yield mock


@pytest.fixture
def mock_personal_feedback() -> Iterator[object]:
    with patch.object(tasks, "send_personal_feedback") as mock:
        yield mock


@pytest.fixture
def stub_publish(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Capture ``current_app.send_task`` instead of reaching a broker."""
    sent: list[tuple] = []
    monkeypatch.setattr(
        tasks.current_app, "send_task", lambda name, args: sent.append((name, args))
    )
    return sent


def _extraction(meeting_id: str) -> dict:
    return ExtractionResult(meeting_id=meeting_id).model_dump(mode="json")


def _gap(meeting_id: str) -> dict:
    return GapReport(meeting_id=meeting_id).model_dump(mode="json")


def _context(meeting_id: str) -> dict:
    return ContextLinks(meeting_id=meeting_id).model_dump(mode="json")


_PAYLOAD = {"extraction": _extraction, "gap": _gap, "context": _context}


def _seed(session: Session, meeting_id: str, source: str) -> bool:
    return service.record_completion(session, meeting_id, source, _PAYLOAD[source](meeting_id))


@pytest.mark.usefixtures("use_test_session")
def test_first_completion_schedules_the_timeout_countdown(
    mock_aggregate: object, db_session: Session, meeting: str
) -> None:
    tasks.on_extraction_completed(_extraction(meeting))

    mock_aggregate.apply_async.assert_called_once()
    args, kwargs = mock_aggregate.apply_async.call_args
    assert args[0] == (meeting,)
    assert kwargs["countdown"] == 600
    assert db_session.get(IntelCompletion, meeting).extraction_at is not None


@pytest.mark.usefixtures("use_test_session")
def test_a_repeated_completion_does_not_schedule_again(
    mock_aggregate: object, meeting: str
) -> None:
    tasks.on_gap_completed(_gap(meeting))
    tasks.on_gap_completed(_gap(meeting))

    mock_aggregate.apply_async.assert_called_once()


@pytest.mark.usefixtures("use_test_session")
def test_the_third_completion_enqueues_aggregation_immediately(
    mock_aggregate: object, db_session: Session, meeting: str
) -> None:
    _seed(db_session, meeting, "extraction")
    _seed(db_session, meeting, "gap")
    db_session.flush()

    tasks.on_context_completed(_context(meeting))

    mock_aggregate.apply_async.assert_called_once_with((meeting,))


@pytest.mark.usefixtures("use_test_session", "mock_personal_feedback")
def test_aggregate_publishes_a_valid_snapshot(
    db_session: Session, meeting: str, team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autune_intelligence import service

    service.record_completion(
        db_session,
        meeting,
        "extraction",
        {"meeting_id": meeting, "decisions": [], "action_items": []},
    )
    db_session.flush()

    sent: list[tuple] = []
    monkeypatch.setattr(
        tasks.current_app, "send_task", lambda name, args: sent.append((name, args))
    )

    tasks.aggregate(meeting)

    assert len(sent) == 1
    name, args = sent[0]
    assert name == "autune.intelligence.completed"
    IntelligenceSnapshot.model_validate(args[0])  # contract conformance


@pytest.mark.usefixtures("use_test_session", "mock_personal_feedback")
def test_aggregate_publishes_once_and_the_second_pass_is_a_no_op(
    db_session: Session, meeting: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(db_session, meeting, "extraction")
    db_session.flush()

    sent: list[tuple] = []
    monkeypatch.setattr(
        tasks.current_app, "send_task", lambda name, args: sent.append((name, args))
    )

    tasks.aggregate(meeting)
    db_session.flush()
    aggregated_at = db_session.get(IntelCompletion, meeting).aggregated_at
    assert aggregated_at is not None

    tasks.aggregate(meeting)  # second run is a no-op: no row change, no publish
    db_session.flush()
    assert db_session.get(IntelCompletion, meeting).aggregated_at == aggregated_at
    assert len(sent) == 1


@pytest.mark.usefixtures("use_test_session")
def test_a_completion_after_the_first_pass_reopens_and_re_enqueues(
    mock_aggregate: object, db_session: Session, meeting: str
) -> None:
    from autune_intelligence import service

    for source in ("extraction", "gap", "context"):
        service.record_completion(
            db_session,
            meeting,
            source,
            {"meeting_id": meeting},
        )
    db_session.flush()
    service.aggregate_meeting(db_session, meeting)
    db_session.flush()

    tasks.on_extraction_completed({"meeting_id": meeting, "decisions": [], "action_items": []})

    assert db_session.get(service.IntelCompletion, meeting).aggregated_at is None
    mock_aggregate.apply_async.assert_called_once_with((meeting,), {"notify": False})


@pytest.mark.usefixtures("use_test_session", "stub_publish")
def test_aggregate_enqueues_personal_feedback_after_a_first_pass(
    mock_personal_feedback: object, db_session: Session, meeting: str, team: str
) -> None:
    service.record_completion(db_session, meeting, "extraction", _extraction(meeting))
    db_session.flush()

    tasks.aggregate(meeting)

    mock_personal_feedback.apply_async.assert_called_once_with((meeting,))


@pytest.mark.usefixtures("use_test_session", "stub_publish")
def test_a_re_aggregation_does_not_enqueue_personal_feedback(
    mock_personal_feedback: object, db_session: Session, meeting: str, team: str
) -> None:
    service.record_completion(db_session, meeting, "extraction", _extraction(meeting))
    db_session.flush()

    tasks.aggregate(meeting, notify=False)

    mock_personal_feedback.apply_async.assert_not_called()


@pytest.mark.usefixtures("use_test_session")
def test_send_personal_feedback_task_skips_a_team_without_slack(
    db_session: Session, meeting: str, team: str
) -> None:
    with patch.object(tasks.service, "send_personal_feedback") as inner:
        tasks.send_personal_feedback(meeting)

    inner.assert_not_called()


# --- generate_weekly_report -------------------------------------------------
#
# Unlike send_personal_feedback (computed, DM'd, discarded — nothing to save
# without a recipient), the weekly report is a persisted intel_reports row the
# dashboard's read API can serve on its own. It is generated whether or not
# Slack is connected; only the channel post is skipped without one.


@pytest.fixture
def fake_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real Fernet key, bypassing AUTUNE_ENCRYPTION_KEY for this test.

    autune_core.crypto._fernet() is itself lru_cached from settings, so a test
    running after any earlier test has already resolved settings cannot fix
    this by setting the environment variable — the cache would already be
    populated. Patching the module attribute directly sidesteps that.
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


@pytest.mark.usefixtures("use_test_session")
def test_generate_weekly_report_writes_the_row_even_without_slack(
    db_session: Session, team: str
) -> None:
    tasks.generate_weekly_report(team, period_end="2026-09-14")

    row = db_session.get(service.IntelReport, (team, date.fromisoformat("2026-09-07")))
    assert row is not None
    assert row.metrics_json["meeting_count"] == 0


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_generate_weekly_report_skips_delivery_without_a_configured_channel(
    db_session: Session, team: str
) -> None:
    _connect_slack(db_session, team, config={})

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.generate_weekly_report(team, period_end="2026-09-14")

    slack_client_cls.assert_not_called()


@pytest.mark.usefixtures("use_test_session", "fake_encryption_key")
def test_generate_weekly_report_posts_to_the_configured_channel(
    db_session: Session, team: str
) -> None:
    _connect_slack(db_session, team, config={"channel": "C123"})

    with patch.object(tasks, "SlackClient") as slack_client_cls:
        tasks.generate_weekly_report(team, period_end="2026-09-14")

    slack_client_cls.return_value.post_message.assert_called_once()
    args, _kwargs = slack_client_cls.return_value.post_message.call_args
    assert args[0] == "C123"
    assert "분석된 회의가 없습니다" in args[1]


@pytest.mark.usefixtures("use_test_session")
def test_default_period_is_the_trailing_seven_days(db_session: Session, team: str) -> None:
    with patch.object(tasks.service, "generate_weekly_report") as inner:
        tasks.generate_weekly_report(team)

    _session, _team_id, period_start, period_end = inner.call_args.args
    assert period_end - period_start == timedelta(days=7)
