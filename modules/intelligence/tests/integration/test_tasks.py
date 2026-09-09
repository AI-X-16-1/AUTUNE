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

from autune_contracts import ContextLinks, ExtractionResult, GapReport
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
def test_a_late_completion_after_aggregation_enqueues_nothing(
    mock_aggregate: object, db_session: Session, meeting: str
) -> None:
    for source in ("extraction", "gap", "context"):
        _seed(db_session, meeting, source)
    db_session.flush()
    service.close_aggregation(db_session, meeting)
    db_session.flush()

    tasks.on_extraction_completed(_extraction(meeting))

    mock_aggregate.apply_async.assert_not_called()


@pytest.mark.usefixtures("use_test_session")
def test_aggregate_task_closes_the_lifecycle_and_is_idempotent(
    db_session: Session, meeting: str
) -> None:
    _seed(db_session, meeting, "extraction")
    db_session.flush()

    tasks.aggregate(meeting)
    db_session.flush()
    aggregated_at = db_session.get(IntelCompletion, meeting).aggregated_at
    assert aggregated_at is not None

    tasks.aggregate(meeting)  # second run is a no-op
    db_session.flush()
    assert db_session.get(IntelCompletion, meeting).aggregated_at == aggregated_at
