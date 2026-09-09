"""Module E completion handlers — enqueue behaviour, against real PostgreSQL.

The handlers open their own ``session_scope``. Each test points that at the
test's transactional ``db_session`` so writes roll back, and mocks ``aggregate``
so nothing is really queued.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
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


@pytest.mark.usefixtures("use_test_session")
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


@pytest.mark.usefixtures("use_test_session")
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
    mock_aggregate.apply_async.assert_called_once_with((meeting,))
