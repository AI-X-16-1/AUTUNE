"""An invitation link mailed from the inviter's own Gmail (#552), against a
real database with Google and Gmail faked.

What is pinned: mail is asked for, never assumed; it goes from the inviter's
own grant and from nobody else's; the invitation is made and its link handed
back whether or not the mail goes; the answer cannot tell one address from
another; and neither the address nor the token reaches a log line.

Read ``conftest.py`` for ``db_session``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from autune_audio import invitation_mail
from autune_audio.models import AudTeamInvitation
from autune_audio.router import router
from autune_core import AutuneError, TeamMember, User, get_session
from autune_core.auth import current_user
from autune_core.user_integrations import UserIntegrationConfig
from autune_integrations.errors import PermanentIntegrationError

INVITED = "Newcomer@Example.com"
CLIENT = "integration-client"


@dataclass
class FakeSettings:
    google_integration_credentials: tuple[str, str] = (CLIENT, "secret")
    web_base_url: str = "https://autune.example/"


class FakeGmail:
    """Stands in for ``GmailClient``; every instance reports to one list."""

    sent: list[dict[str, Any]] = []
    fail: Exception | None = None
    tokens: list[str] = []

    def __init__(self, access_token: str) -> None:
        FakeGmail.tokens.append(access_token)

    def send(self, **message: Any) -> str:
        if FakeGmail.fail is not None:
            raise FakeGmail.fail
        FakeGmail.sent.append(message)
        return "msg-1"

    def close(self) -> None:
        pass


@pytest.fixture
def grants(monkeypatch: pytest.MonkeyPatch) -> dict[str, UserIntegrationConfig]:
    """Gmail grants by person, and Google faked around them."""
    held: dict[str, UserIntegrationConfig] = {}
    refreshed: list[str] = []
    FakeGmail.sent, FakeGmail.fail, FakeGmail.tokens = [], None, []

    def load(_s: Session, user_id: str, service: str) -> UserIntegrationConfig | None:
        assert service == "gmail_send"
        return held.get(user_id)

    def refresh(*, client_id: str, client_secret: str, refresh_token: str) -> str:
        refreshed.append(refresh_token)
        return f"access-for-{refresh_token}"

    monkeypatch.setattr(invitation_mail, "load_user_integration", load)
    monkeypatch.setattr(invitation_mail, "get_core_settings", FakeSettings)
    monkeypatch.setattr(invitation_mail, "refresh_access_token", refresh)
    monkeypatch.setattr(invitation_mail, "GmailClient", FakeGmail)
    held["__refreshed__"] = refreshed  # type: ignore[assignment]
    return held


def connect(grants: dict[str, Any], user: User, *, client_id: str = CLIENT) -> None:
    grants[user.id] = UserIntegrationConfig(
        "gmail_send", user.id, f"1//{user.id}", {"client_id": client_id}
    )


@pytest.fixture
def inviter(db_session: Session, team: str) -> User:
    user = User(email="host@example.com", display_name="초대한 사람")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


@pytest.fixture
def client(db_session: Session, inviter: User) -> TestClient:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix="/api/audio")
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[current_user] = lambda: inviter
    return TestClient(app)


def invite(client: TestClient, team: str, *, send_email: bool, email: str = INVITED):
    return client.post(
        f"/api/audio/teams/{team}/invitations", json={"email": email, "send_email": send_email}
    )


def pending(session: Session, team: str) -> int:
    session.expire_all()
    return len(
        session.scalars(sa.select(AudTeamInvitation).where(AudTeamInvitation.team_id == team)).all()
    )


def test_no_mail_goes_unless_it_is_asked_for(
    client: TestClient, grants: dict[str, Any], inviter: User, team: str
) -> None:
    connect(grants, inviter)

    body = invite(client, team, send_email=False).json()

    assert body["emailed"] is False
    assert FakeGmail.sent == []


def test_the_link_is_mailed_from_the_inviters_own_grant(
    db_session: Session, client: TestClient, grants: dict[str, Any], inviter: User, team: str
) -> None:
    connect(grants, inviter)

    response = invite(client, team, send_email=True)

    assert response.status_code == 201
    body = response.json()
    assert body["emailed"] is True
    assert grants["__refreshed__"] == [f"1//{inviter.id}"]
    assert FakeGmail.tokens == [f"access-for-1//{inviter.id}"]
    (mail,) = FakeGmail.sent
    link = f"https://autune.example/invite#{body['token']}"
    assert mail["to"] == INVITED
    assert link in mail["body"]
    assert mail["unchecked"] == [link]
    assert "초대한 사람" in mail["subject"] and "Test Team" in mail["subject"]
    # The recipient knows their own address; the message does not repeat it.
    assert INVITED.lower() not in (mail["subject"] + mail["body"]).lower()
    # The link in the mail is the invitation, committed before it was sent.
    assert pending(db_session, team) == 1


def test_without_a_grant_the_invitation_is_made_and_not_mailed(
    db_session: Session, client: TestClient, grants: dict[str, Any], team: str
) -> None:
    body = invite(client, team, send_email=True).json()

    assert body["emailed"] is False
    assert body["token"]
    assert FakeGmail.sent == []
    assert pending(db_session, team) == 1


def test_a_grant_from_another_google_client_is_not_tried(
    client: TestClient, grants: dict[str, Any], inviter: User, team: str
) -> None:
    connect(grants, inviter, client_id="an-older-client")

    assert invite(client, team, send_email=True).json()["emailed"] is False
    assert grants["__refreshed__"] == []


def test_a_refused_send_leaves_a_working_link_and_logs_no_address_or_token(
    db_session: Session, client: TestClient, grants: dict[str, Any], inviter: User, team: str
) -> None:
    connect(grants, inviter)
    FakeGmail.fail = PermanentIntegrationError("gmail rejected the request with 403")

    with capture_logs() as logs:
        body = invite(client, team, send_email=True).json()

    assert body["emailed"] is False
    assert pending(db_session, team) == 1
    (entry,) = [e for e in logs if e["event"] == "team_invitation_not_mailed"]
    assert entry["reason"] == "integration_rejected"
    written = repr(logs).lower()
    assert INVITED.lower() not in written
    assert body["token"].lower() not in written
    assert hashlib.sha256(body["token"].encode()).hexdigest() not in written


def test_the_answer_is_the_same_shape_whatever_the_address(
    client: TestClient, grants: dict[str, Any], inviter: User, team: str
) -> None:
    connect(grants, inviter)

    answers = [
        invite(client, team, send_email=True, email=email).json()
        for email in ("nobody-signed-up@example.com", inviter.email)
    ]

    assert {frozenset(a) for a in answers} == {frozenset({"token", "expires_at", "emailed"})}
    assert {a["emailed"] for a in answers} == {True}
