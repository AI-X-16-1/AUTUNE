"""A person's change to a meeting report is never left without an approval request (#698).

The card's routes commit a change, then queue its announcement. Queueing can fail
after the commit; an unchanged save is refused, so the person could not ask
again. The announcement records which change it covered, and a periodic sweep
announces any change none covered. A correction whose team lost its Slack after
the post reads as failed instead of waiting forever.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_contracts import INTELLIGENCE_MEETING_REPORT_CHANGED
from autune_core import AutuneError, Meeting, TeamIntegration, TeamMember, User, get_session
from autune_core.auth import current_user
from autune_intelligence import service, tasks
from autune_intelligence.models import IntelMeetingReport
from autune_intelligence.router import router

BODY = "✅ 확정된 액션 아이템\n• 결제 API 스펙 초안 — 백엔드 · 10/2"
LATER = timedelta(minutes=3)
"""Past ``ANNOUNCE_RETRY_AFTER``: the sweep's view of a change made now."""


def _user(db_session: Session, team: str, name: str = "이승환") -> User:
    user = User(email=f"{name}-{uuid.uuid4().hex}@example.com", display_name=name)
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


@pytest.fixture
def client_for(db_session: Session) -> Iterator[Callable[[User], TestClient]]:
    def build(user: User) -> TestClient:
        app = FastAPI()

        @app.exception_handler(AutuneError)
        async def render(_: Request, exc: AutuneError) -> JSONResponse:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

        app.include_router(router, prefix="/api/intelligence")
        app.dependency_overrides[get_session] = lambda: db_session
        app.dependency_overrides[current_user] = lambda: user
        return TestClient(app)

    yield build


@pytest.fixture
def published(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Meeting ids the announce task published, run on the test's session."""

    @contextlib.contextmanager
    def scope() -> Iterator[Session]:
        yield db_session

    sent: list[str] = []

    def publish(event: str, payload: dict[str, Any]) -> list[str]:
        assert event == INTELLIGENCE_MEETING_REPORT_CHANGED
        sent.append(payload["meeting_id"])
        return []

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "publish", publish)
    return sent


@pytest.fixture
def queue_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """The broker refuses the card route's announcement."""
    from autune_intelligence import router as router_module

    def refused(_meeting_id: str) -> None:
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(router_module.enqueue, "announce_meeting_report_changed", refused)


@pytest.fixture
def queue_up(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from autune_intelligence import router as router_module

    queued: list[str] = []
    monkeypatch.setattr(router_module.enqueue, "announce_meeting_report_changed", queued.append)
    return queued


def _draft(db_session: Session, team: str) -> str:
    meeting = Meeting(
        team_id=team, title="결제 회의", started_at=datetime(2026, 10, 2, 5, tzinfo=UTC)
    )
    db_session.add(meeting)
    db_session.flush()
    document = service.meeting_report_document(meeting, BODY)
    service.save_meeting_report(db_session, meeting.id, document, draft_id="rdr_model")
    return meeting.id


def _posted(db_session: Session, team: str) -> str:
    meeting = _draft(db_session, team)
    service.claim_meeting_report(db_session, meeting, draft_id="rdr_model")
    service.record_meeting_report_post(db_session, meeting, "C123", "1.000100")
    db_session.add(_slack(team))
    db_session.flush()
    return meeting


def _slack(team: str) -> TeamIntegration:
    # A token and a channel; the token is checked for, never decrypted, here.
    return TeamIntegration(
        team_id=team, service="slack", config={"channel": "C123"}, secret="stored-token"
    )


def _edit(client: TestClient, meeting: str, body: str = "✅ 고친 본문") -> Any:
    return client.put(f"/api/intelligence/meeting-reports/{meeting}", json={"body": body})


def _correct(client: TestClient, meeting: str, body: str = "✅ 정정") -> Any:
    return client.post(
        f"/api/intelligence/meeting-reports/{meeting}/corrections", json={"body": body}
    )


def _swept(db_session: Session) -> list[str]:
    return service.meeting_reports_unannounced(db_session, now=datetime.now(UTC) + LATER)


# --- a lost announcement -------------------------------------------------------------


@pytest.mark.usefixtures("queue_down")
def test_an_edit_whose_announcement_could_not_be_queued_is_saved_and_swept(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _draft(db_session, team)

    response = _edit(client_for(_user(db_session, team)), meeting)

    assert response.status_code == 200  # saved; a refused queue is not the person's error
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.edited_at is not None and row.announced_at is None
    assert _swept(db_session) == [meeting]


@pytest.mark.usefixtures("queue_down")
def test_a_correction_whose_announcement_could_not_be_queued_is_swept(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _posted(db_session, team)

    response = _correct(client_for(_user(db_session, team)), meeting)

    assert response.status_code == 202
    assert _swept(db_session) == [meeting]


@pytest.mark.usefixtures("queue_up")
def test_the_sweep_announces_it_once(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    published: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meeting = _draft(db_session, team)
    _edit(client_for(_user(db_session, team)), meeting)  # queued, but the task never ran
    monkeypatch.setattr(tasks, "datetime", _clock(datetime.now(UTC) + LATER))

    first = tasks.announce_report_changes()
    second = tasks.announce_report_changes()

    assert first == [meeting] and published == [meeting]
    assert second == []  # the announcement recorded what it covered


@pytest.mark.usefixtures("queue_up")
def test_a_fresh_change_is_left_to_its_own_announcement(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _draft(db_session, team)
    _edit(client_for(_user(db_session, team)), meeting)

    assert service.meeting_reports_unannounced(db_session, now=datetime.now(UTC)) == []


@pytest.mark.usefixtures("queue_up")
def test_a_change_after_the_announcement_is_swept_again(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    published: list[str],
) -> None:
    meeting = _draft(db_session, team)
    client = client_for(_user(db_session, team))
    _edit(client, meeting)
    tasks.announce_meeting_report_changed(meeting)
    assert _swept(db_session) == []

    _edit(client, meeting, "✅ 다시 고친 본문")  # its own announcement was lost

    assert _swept(db_session) == [meeting]


@pytest.mark.usefixtures("queue_up")
def test_nothing_of_a_persons_waiting_is_not_announced(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    published: list[str],
) -> None:
    """A model's draft (its run proposes it), a rerun over an edit, a posted report."""
    model = _draft(db_session, team)
    rerun = _draft(db_session, team)
    _edit(client_for(_user(db_session, team)), rerun)
    meeting = db_session.get(Meeting, rerun)
    assert meeting is not None
    service.save_meeting_report(
        db_session, rerun, service.meeting_report_document(meeting, BODY), draft_id="rdr_rerun"
    )
    posted = _posted(db_session, team)

    for each in (model, rerun, posted):
        tasks.announce_meeting_report_changed(each)

    assert published == [] and _swept(db_session) == []


@pytest.mark.usefixtures("queue_up")
def test_the_routes_task_after_the_sweep_announces_nothing(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    published: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route's task sat in a slow queue past the sweep: one announcement, one DM (#705)."""
    meeting = _draft(db_session, team)
    _edit(client_for(_user(db_session, team)), meeting)  # its task is still queued
    monkeypatch.setattr(tasks, "datetime", _clock(datetime.now(UTC) + LATER))
    tasks.announce_report_changes()

    tasks.announce_meeting_report_changed(meeting)  # the queued task, at last
    tasks.announce_meeting_report_changed(meeting)  # and redelivered (acks_late)

    assert published == [meeting]


@pytest.mark.usefixtures("queue_up")
def test_a_failed_publish_gives_the_change_back_to_the_sweep(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    published: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meeting = _draft(db_session, team)
    _edit(client_for(_user(db_session, team)), meeting)

    def down(_event: str, _payload: dict[str, Any]) -> list[str]:
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(tasks, "publish", down)
    with pytest.raises(ConnectionError):
        tasks.announce_meeting_report_changed(meeting)

    assert _swept(db_session) == [meeting]


@pytest.mark.usefixtures("queue_up")
def test_one_meeting_that_fails_does_not_hold_back_the_sweep(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    published: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = client_for(_user(db_session, team))
    first, second = _draft(db_session, team), _draft(db_session, team)
    _edit(client, first)
    _edit(client, second)
    real = tasks.publish

    def flaky(event: str, payload: dict[str, Any]) -> list[str]:
        if payload["meeting_id"] == first:
            raise ConnectionError("broker unreachable")
        return real(event, payload)

    monkeypatch.setattr(tasks, "publish", flaky)
    monkeypatch.setattr(tasks, "datetime", _clock(datetime.now(UTC) + LATER))

    swept = tasks.announce_report_changes()

    assert set(swept) == {first, second} and published == [second]


@pytest.mark.usefixtures("queue_up")
def test_a_save_that_changes_only_invisible_whitespace_is_unchanged(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _draft(db_session, team)
    client = client_for(_user(db_session, team))
    [shown] = client.get(f"/api/intelligence/meeting-reports/{team}").json()
    padded = "\n".join(line + "  " for line in shown["body"].splitlines()) + "\n\n"

    response = _edit(client, meeting, padded)

    assert response.status_code == 422 and "unchanged" in response.text


# --- a team whose Slack went away after the post -----------------------------------


@pytest.mark.usefixtures("queue_up")
def test_a_correction_is_refused_once_slack_is_disconnected(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _posted(db_session, team)
    db_session.execute(sa.delete(TeamIntegration).where(TeamIntegration.team_id == team))
    db_session.flush()

    response = _correct(client_for(_user(db_session, team)), meeting)

    assert response.status_code == 409 and "slack is not connected" in response.text


@pytest.mark.usefixtures("queue_up")
def test_an_approved_correction_with_no_slack_left_reads_failed_not_waiting(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    published: list[str],
) -> None:
    meeting = _posted(db_session, team)
    client = client_for(_user(db_session, team))
    _correct(client, meeting)
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.correction_id is not None
    correction = row.correction_id
    db_session.execute(sa.delete(TeamIntegration).where(TeamIntegration.team_id == team))
    db_session.flush()

    tasks.deliver_meeting_report_correction(meeting, correction)  # approved, nowhere to post

    [shown] = client.get(f"/api/intelligence/meeting-reports/{team}").json()
    assert shown["correction_status"] == "failed"
    assert service.meeting_report_awaiting_approval(db_session, meeting) is None
    assert _swept(db_session) == []  # not proposed again either

    # Reconnected: a new correction is accepted and waits again.
    db_session.add(_slack(team))
    db_session.flush()
    again = _correct(client, meeting, "✅ 다시 정정")
    assert again.status_code == 202 and again.json()["correction_status"] == "pending"


@pytest.mark.usefixtures("queue_up")
def test_a_slack_connection_without_a_token_counts_as_disconnected(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    published: list[str],
) -> None:
    """The channel is set but the token is gone: refused when written, failed when approved."""
    meeting = _posted(db_session, team)
    client = client_for(_user(db_session, team))
    _correct(client, meeting)
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.correction_id is not None
    db_session.execute(
        sa.update(TeamIntegration).where(TeamIntegration.team_id == team).values(secret=None)
    )
    db_session.flush()

    tasks.deliver_meeting_report_correction(meeting, row.correction_id)
    refused = _correct(client, meeting, "✅ 다시 정정")

    [shown] = client.get(f"/api/intelligence/meeting-reports/{team}").json()
    assert shown["correction_status"] == "failed"
    assert refused.status_code == 409 and "slack is not connected" in refused.text


def _clock(now: datetime) -> Any:
    """``service.datetime`` with ``now`` fixed, everything else the real class."""

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return now if tz is None else now.astimezone(tz)

    return Clock
