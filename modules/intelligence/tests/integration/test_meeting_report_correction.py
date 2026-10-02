"""A correction to a posted meeting report, approved, then posted in its Slack thread (10/2).

A posted report is never changed in place: people have read it. A member of
the team writes a correction on the dashboard card. Like an edit it waits for a
person with the ``report`` scope (#674): the card announces it, the Report
subagent proposes ``publish_meeting_report_correction`` with its
``correction_id``, and the approved one goes out as a reply under the original
post -- or as a new message in the team's channel when that thread is out of
reach. It is checked like any report text, escaped for Slack, and sent at most
once.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_core import AutuneError, Meeting, Team, TeamMember, User, get_session
from autune_core.auth import current_user
from autune_integrations import PermanentIntegrationError, TransientIntegrationError
from autune_integrations.fakes import FakeSlack
from autune_integrations.privacy import MAX_OUTBOUND_CHARS
from autune_intelligence import service, tasks, tools
from autune_intelligence.models import IntelMeetingReport
from autune_intelligence.router import router

BODY = "✅ 확정된 액션 아이템\n• 결제 API 스펙 초안 — 백엔드 · 10/2"


def _user(db_session: Session, team: str | None, name: str = "이승환") -> User:
    user = User(email=f"{name}-{uuid.uuid4().hex}@example.com", display_name=name)
    db_session.add(user)
    db_session.flush()
    if team is not None:
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


@pytest.fixture(autouse=True)
def announced(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What the card's route announced -- meeting ids only (#275)."""
    from autune_intelligence import router as router_module

    sent: list[str] = []
    monkeypatch.setattr(router_module.enqueue, "announce_meeting_report_changed", sent.append)
    return sent


def _posted_report(
    db_session: Session,
    team: str,
    *,
    channel: str | None = "C123",
    ts: str | None = "1.000100",
    title: str = "결제 회의",
) -> str:
    meeting = Meeting(team_id=team, title=title, started_at=datetime(2026, 10, 2, 5, tzinfo=UTC))
    db_session.add(meeting)
    db_session.flush()
    document = service.meeting_report_document(meeting, BODY)
    service.save_meeting_report(db_session, meeting.id, document, draft_id="rdr_a")
    service.claim_meeting_report(db_session, meeting.id, draft_id="rdr_a")
    if channel is not None and ts is not None:
        service.record_meeting_report_post(db_session, meeting.id, channel, ts)
        _link_slack(db_session, team, channel)
    return meeting.id


def _link_slack(
    db_session: Session, team_id: str, channel: str, *, secret: str | None = None
) -> None:
    """The team's Slack connection: a correction is refused without one (#698)."""
    from autune_core import TeamIntegration

    row = db_session.scalar(
        sa.select(TeamIntegration).where(
            TeamIntegration.team_id == team_id, TeamIntegration.service == "slack"
        )
    )
    if row is None:
        row = TeamIntegration(team_id=team_id, service="slack", config={})
        db_session.add(row)
    row.config = {"channel": channel}
    if secret is not None:
        row.secret = secret
    db_session.flush()


def _url(meeting: str) -> str:
    return f"/api/intelligence/meeting-reports/{meeting}/corrections"


def _correction_id(db_session: Session, meeting: str) -> str:
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.correction_id is not None
    return row.correction_id


# --- writing one -------------------------------------------------------------------


def test_a_correction_waits_for_approval_and_nothing_is_posted(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    announced: list[str],
) -> None:
    meeting = _posted_report(db_session, team)

    response = client_for(_user(db_session, team, "박재경")).post(
        _url(meeting), json={"body": "✅ 기한을 10/3으로 바로잡습니다"}
    )

    assert response.status_code == 202
    data = response.json()
    assert data["correction_body"] == "✅ 기한을 10/3으로 바로잡습니다"
    assert (data["corrected_by_name"], data["correction_status"]) == ("박재경", "pending")
    assert announced == [meeting]  # the Report subagent proposes its post (#674)
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.correction_id is not None
    assert row.correction_id.startswith("rcr_") and row.correction_sent_at is None
    assert row.body_markdown.endswith("기준.")  # the post itself is untouched


def test_a_draft_is_edited_not_corrected(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    announced: list[str],
) -> None:
    meeting = Meeting(team_id=team, title="결제 회의")
    db_session.add(meeting)
    db_session.flush()
    service.save_meeting_report(
        db_session, meeting.id, service.meeting_report_document(meeting, BODY), draft_id="rdr_a"
    )

    response = client_for(_user(db_session, team)).post(_url(meeting.id), json={"body": "x"})

    assert response.status_code == 409 and announced == []


def test_a_report_that_never_reached_slack_takes_no_correction(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    announced: list[str],
) -> None:
    """Claimed, then the post failed: the channel never saw the original (#658 review)."""
    meeting = _posted_report(db_session, team, channel=None, ts=None)
    client = client_for(_user(db_session, team))

    response = client.post(_url(meeting), json={"body": "✅ 정정"})
    [shown] = client.get(f"/api/intelligence/meeting-reports/{team}").json()

    assert response.status_code == 409 and "did not reach slack" in response.text
    assert announced == [] and shown["in_slack"] is False


def test_a_newer_correction_replaces_one_still_waiting(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The approval pins the id: approving the first after the second posts nothing."""
    monkeypatch.setattr(tools, "session_scope", _scope(db_session))
    meeting = _posted_report(db_session, team)
    client = client_for(_user(db_session, team))
    client.post(_url(meeting), json={"body": "✅ 첫 수정"})
    first = _correction_id(db_session, meeting)

    second = client.post(_url(meeting), json={"body": "✅ 두 번째 수정"})
    stale = tools.publish_meeting_report_correction(team, meeting, first)

    assert second.status_code == 202 and _correction_id(db_session, meeting) != first
    assert stale["ok"] is False and stale["reason"] == "correction not current"
    with pytest.raises(AutuneError):
        service.claim_meeting_report_correction(db_session, meeting, correction_id=first)


def test_the_same_text_again_is_refused_and_not_announced(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    announced: list[str],
) -> None:
    meeting = _posted_report(db_session, team)
    client = client_for(_user(db_session, team))
    client.post(_url(meeting), json={"body": "✅ 정정"})

    again = client.post(_url(meeting), json={"body": "✅ 정정"})

    assert again.status_code == 422 and announced == [meeting]


def test_personal_data_empty_or_too_long_is_refused(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    announced: list[str],
) -> None:
    meeting = _posted_report(db_session, team)
    client = client_for(_user(db_session, team))

    phone = client.post(_url(meeting), json={"body": "연락처 010-1234-5678"})
    empty = client.post(_url(meeting), json={"body": "  "})
    long = client.post(_url(meeting), json={"body": "가" * 3000})
    escaped = client.post(_url(meeting), json={"body": "&" * 600})  # 3,000 in Slack

    assert [r.status_code for r in (phone, empty, long, escaped)] == [422] * 4
    assert "010-1234-5678" not in phone.text
    assert announced == []


def test_someone_outside_the_team_gets_not_found(
    db_session: Session,
    team: str,
    client_for: Callable[[User], TestClient],
    announced: list[str],
) -> None:
    meeting = _posted_report(db_session, team)
    other = Team(name="Other Team")
    db_session.add(other)
    db_session.flush()
    theirs = _posted_report(db_session, other.id)

    outsider = client_for(_user(db_session, None)).post(_url(meeting), json={"body": "x"})
    member = client_for(_user(db_session, team)).post(_url(theirs), json={"body": "x"})

    assert (outsider.status_code, member.status_code) == (404, 404) and announced == []


# --- approved, then sent -----------------------------------------------------------


def _scope(db_session: Session) -> Callable[[], contextlib.AbstractContextManager[Session]]:
    @contextlib.contextmanager
    def scope() -> Iterator[Session]:
        yield db_session

    return scope


def _connect_slack(db_session: Session, team_id: str, channel: str = "C123") -> None:
    from autune_core.crypto import encrypt

    _link_slack(db_session, team_id, channel, secret=encrypt("xoxb-test"))


@pytest.fixture
def ready(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    from autune_core import crypto

    key = Fernet(Fernet.generate_key())
    monkeypatch.setattr(crypto, "_fernet", lambda: key)
    monkeypatch.setattr(tasks, "session_scope", _scope(db_session))
    monkeypatch.setattr(tools, "session_scope", _scope(db_session))


def _approve(team: str, meeting: str, correction_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """What the main agent runs once a person approves: E's L2 action."""
    queued: list[tuple[str, ...]] = []
    monkeypatch.setattr(tasks.deliver_meeting_report_correction, "apply_async", queued.append)
    result = tools.publish_meeting_report_correction(team, meeting, correction_id)
    assert result["ok"] is True and queued == [(meeting, correction_id)]  # ids only (#275)


def _write(db_session: Session, team: str, meeting: str, body: str, name: str = "이승환") -> str:
    service.correct_meeting_report(
        db_session, meeting, body, user_id=_user(db_session, team, name).id
    )
    return _correction_id(db_session, meeting)


@pytest.mark.usefixtures("ready")
def test_an_approved_correction_goes_under_the_original_post(
    db_session: Session, team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = _posted_report(db_session, team, channel="C123", ts="1.000100")
    _connect_slack(db_session, team)
    correction = _write(db_session, team, meeting, "✅ 기한 <!channel> 정정", name="박재경")
    slack = FakeSlack()

    _approve(team, meeting, correction, monkeypatch)
    with patch.object(tasks, "SlackClient", return_value=slack):
        tasks.deliver_meeting_report_correction(meeting, correction)

    [message] = slack.sent
    assert (message.channel, message.thread_ts) == ("C123", "1.000100")
    assert message.text.startswith("✏️ 수정본 · ") and "박재경" in message.text
    assert "<!channel>" not in message.text and "&lt;!channel&gt;" in message.text
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.correction_slack_ts is not None


@pytest.mark.usefixtures("ready")
def test_a_correction_is_sent_once(db_session: Session, team: str) -> None:
    meeting = _posted_report(db_session, team)
    _connect_slack(db_session, team)
    correction = _write(db_session, team, meeting, "✅ 정정")
    slack = FakeSlack()

    with patch.object(tasks, "SlackClient", return_value=slack):
        tasks.deliver_meeting_report_correction(meeting, correction)
        tasks.deliver_meeting_report_correction(meeting, correction)

    assert len(slack.sent) == 1


@pytest.mark.usefixtures("ready")
def test_a_correction_written_after_the_approval_is_not_posted_under_it(
    db_session: Session, team: str
) -> None:
    """Approved A, then B replaced it before the worker claimed A: nothing goes out."""
    meeting = _posted_report(db_session, team)
    _connect_slack(db_session, team)
    approved = _write(db_session, team, meeting, "✅ 첫 수정")
    _write(db_session, team, meeting, "✅ 두 번째 수정")
    slack = FakeSlack()

    with patch.object(tasks, "SlackClient", return_value=slack):
        tasks.deliver_meeting_report_correction(meeting, approved)

    assert slack.sent == []
    awaiting = service.meeting_report_awaiting_approval(db_session, meeting)
    assert awaiting is not None and awaiting.kind == "correction" and awaiting.id != approved


@pytest.mark.usefixtures("ready")
def test_a_thread_slack_refuses_falls_back_to_a_new_message(db_session: Session, team: str) -> None:
    """The workspace reconnected (a new bot or channel): the old thread is out of reach."""
    meeting = _posted_report(db_session, team, channel="C_OLD", ts="1.000100")
    _connect_slack(db_session, team, channel="C_NEW")
    correction = _write(db_session, team, meeting, "✅ 정정")
    slack = FakeSlack()

    def refused(*_args: object, **_kwargs: object) -> str:
        raise PermanentIntegrationError("channel_not_found")

    with (
        patch.object(tasks, "SlackClient", return_value=slack),
        patch.object(slack, "reply_in_thread", side_effect=refused),
    ):
        tasks.deliver_meeting_report_correction(meeting, correction)

    [message] = slack.sent
    assert (message.channel, message.thread_ts) == ("C_NEW", None)
    assert "원래 게시물" in message.text and "결제 회의" in message.text


@pytest.mark.usefixtures("ready")
def test_the_new_message_stays_under_the_outbound_cap_whatever_the_title(
    db_session: Session, team: str
) -> None:
    """The longest correction, the longest name, a title of "&": still one message (#658 review)."""
    meeting = _posted_report(db_session, team, channel="C_OLD", title="&" * 400)
    _connect_slack(db_session, team, channel="C_NEW")
    correction = _write(db_session, team, meeting, "가" * 2900, name="김" * 200)
    slack = FakeSlack()

    def refused(*_args: object, **_kwargs: object) -> str:
        raise PermanentIntegrationError("channel_not_found")

    with (
        patch.object(tasks, "SlackClient", return_value=slack),
        patch.object(slack, "reply_in_thread", side_effect=refused),
    ):
        tasks.deliver_meeting_report_correction(meeting, correction)

    [message] = slack.sent
    assert len(message.text) <= MAX_OUTBOUND_CHARS


# --- a send that never finished (#658 review) -------------------------------------


def _past_the_window(db_session: Session, meeting: str) -> None:
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.correction_sent_at is not None
    row.correction_sent_at -= service.CORRECTION_SEND_WINDOW + timedelta(seconds=1)
    db_session.flush()


def test_waiting_for_approval_is_pending_however_long_it_takes(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    """The window runs from the claim, not from writing: approval may take days."""
    meeting = _posted_report(db_session, team)
    _write(db_session, team, meeting, "✅ 정정")
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.corrected_at is not None
    row.corrected_at -= timedelta(days=3)
    db_session.flush()

    [shown] = (
        client_for(_user(db_session, team)).get(f"/api/intelligence/meeting-reports/{team}").json()
    )

    assert shown["correction_status"] == "pending"


@pytest.mark.usefixtures("ready")
def test_a_correction_slack_failed_after_the_claim_can_be_written_again(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _posted_report(db_session, team)
    _connect_slack(db_session, team)
    client = client_for(_user(db_session, team))
    client.post(_url(meeting), json={"body": "✅ 첫 수정"})
    first = _correction_id(db_session, meeting)
    slack = FakeSlack()

    def down(*_args: object, **_kwargs: object) -> str:
        raise TransientIntegrationError("slack is down")

    with (
        patch.object(tasks, "SlackClient", return_value=slack),
        patch.object(slack, "reply_in_thread", side_effect=down),
        pytest.raises(TransientIntegrationError),
    ):
        tasks.deliver_meeting_report_correction(meeting, first)
    too_soon = client.post(_url(meeting), json={"body": "✅ 다시 보냄"})
    _past_the_window(db_session, meeting)
    [shown] = client.get(f"/api/intelligence/meeting-reports/{team}").json()
    again = client.post(_url(meeting), json={"body": "✅ 다시 보냄"})

    assert too_soon.status_code == 409
    assert shown["correction_status"] == "failed"
    assert again.status_code == 202 and again.json()["correction_status"] == "pending"


def test_a_late_send_does_not_mark_a_newer_correction_sent(db_session: Session, team: str) -> None:
    """The old task finishes after a newer correction replaced its own."""
    meeting = _posted_report(db_session, team)
    first = _write(db_session, team, meeting, "✅ 첫 수정")
    stale = service.claim_meeting_report_correction(db_session, meeting, correction_id=first)
    assert stale is not None
    _past_the_window(db_session, meeting)
    _write(db_session, team, meeting, "✅ 두 번째")

    service.record_meeting_report_correction(
        db_session, meeting, "9.000900", correction_id=stale.correction_id
    )

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.correction_slack_ts is None


# --- what the agent and the card read ----------------------------------------------


def test_the_report_subagent_reads_the_waiting_correction(db_session: Session, team: str) -> None:
    meeting = _posted_report(db_session, team)
    correction = _write(db_session, team, meeting, "✅ 정정", name="문민재")

    awaiting = tools.meeting_report_awaiting_approval(db_session, team, meeting)
    preview = tools.meeting_report_correction(db_session, team, meeting, correction)
    _write(db_session, team, meeting, "✅ 다시 정정")
    replaced = tools.meeting_report_correction(db_session, team, meeting, correction)

    [item] = awaiting["items"]
    assert (item["kind"], item["correction_id"]) == ("correction", correction)
    assert "정정" not in str(awaiting)  # the id, never the text
    [shown] = preview["items"]
    assert shown["title"].startswith("✏️ 수정본") and "문민재" in shown["title"]
    assert shown["body"] == "✅ 정정"
    assert replaced["ok"] is True and replaced["items"] == []


def test_the_correctors_name_goes_with_their_account(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    """Only the text, its id and the person's id are stored (invariant 11)."""
    meeting = _posted_report(db_session, team)
    member = _user(db_session, team, "문민재")
    service.correct_meeting_report(db_session, meeting, "✅ 정정", user_id=member.id)

    db_session.execute(sa.delete(TeamMember).where(TeamMember.user_id == member.id))
    db_session.execute(sa.delete(User).where(User.id == member.id))
    db_session.expire_all()

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.corrected_by is None and row.correction_body == "✅ 정정"
    [shown] = (
        client_for(_user(db_session, team, "박재경"))
        .get(f"/api/intelligence/meeting-reports/{team}")
        .json()
    )
    assert shown["corrected_by_name"] is None and "문민재" not in str(shown)


@pytest.mark.usefixtures("ready")
def test_a_name_that_looks_like_personal_data_is_left_out_of_the_correction(
    db_session: Session, team: str
) -> None:
    """The member cannot fix their display name by editing the text, so the name is dropped."""
    meeting = _posted_report(db_session, team)
    _connect_slack(db_session, team)
    correction = _write(db_session, team, meeting, "✅ 정정", name="010-1234-5678")
    slack = FakeSlack()

    with patch.object(tasks, "SlackClient", return_value=slack):
        tasks.deliver_meeting_report_correction(meeting, correction)

    [message] = slack.sent
    assert "010-1234-5678" not in message.text and "팀원" in message.text


def test_correcting_an_edited_report_keeps_its_editors_name(
    db_session: Session, team: str, client_for: Callable[[User], TestClient]
) -> None:
    meeting = _posted_report(db_session, team)
    editor = _user(db_session, team, "박재경")
    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None
    row.edited_by = editor.id
    db_session.flush()

    data = (
        client_for(_user(db_session, team, "문민재"))
        .post(_url(meeting), json={"body": "✅ 정정"})
        .json()
    )

    assert (data["edited_by_name"], data["corrected_by_name"]) == ("박재경", "문민재")


def test_a_correction_the_outbound_check_refuses_stays_unclaimed(
    db_session: Session, team: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checked before the claim, as the report is: a refusal claims nothing (#658 review)."""
    from autune_core.errors import PrivacyViolationError

    meeting = _posted_report(db_session, team)
    correction = _write(db_session, team, meeting, "✅ 정정")

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PrivacyViolationError("refused", categories=["phone"])

    monkeypatch.setattr(service, "check_outbound", refuse)
    with pytest.raises(PrivacyViolationError):
        service.claim_meeting_report_correction(db_session, meeting, correction_id=correction)

    row = db_session.get(IntelMeetingReport, meeting)
    assert row is not None and row.correction_sent_at is None
