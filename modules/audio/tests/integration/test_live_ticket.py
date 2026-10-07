"""The live ticket: what lets a socket on another host know who is calling.

The page asks for it over its own origin, where the session cookie goes, and
sends it in ``hello`` to a socket on the API's address, where the cookie does
not. It must be refused where that socket would refuse the person, and it must
open nothing but that socket: not another route, and not a fresh ticket
(review of #982). The socket's side is in ``test_live_routes.py``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_audio import service
from autune_audio.live import tickets
from autune_audio.router import router
from autune_core import AutuneError, TeamMember, User, get_session
from autune_core.auth import current_user, issue_token
from autune_core.errors import PermissionDeniedError


@pytest.fixture(autouse=True)
def no_tickets() -> Iterator[None]:
    tickets.clear()
    yield
    tickets.clear()


@pytest.fixture
def member(db_session: Session, team: str) -> User:
    user = User(email="member@example.com", display_name="팀원")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


@pytest.fixture
def outsider(db_session: Session) -> User:
    user = User(email="outsider@example.com", display_name="남")
    db_session.add(user)
    db_session.flush()
    return user


def app_for(db_session: Session) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix="/api/audio")
    app.dependency_overrides[get_session] = lambda: db_session
    return app


@pytest.fixture
def client_for(db_session: Session):
    """Signed in as ``user``, without going through a token."""

    def build(user: User) -> TestClient:
        app = app_for(db_session)
        app.dependency_overrides[current_user] = lambda: user
        return TestClient(app)

    return build


@pytest.fixture
def bearer_client(db_session: Session) -> TestClient:
    """The real ``current_user``: whatever token the request carries is judged."""
    return TestClient(app_for(db_session))


def test_a_member_gets_a_ticket(client_for, meeting: str, member: User) -> None:
    response = client_for(member).post(f"/api/audio/live/{meeting}/ticket")

    assert response.status_code == 200
    body = response.json()
    assert body["token"].startswith(tickets.PREFIX)
    assert body["expires_in"] == 60


def test_an_outsider_gets_no_ticket(client_for, meeting: str, outsider: User) -> None:
    response = client_for(outsider).post(f"/api/audio/live/{meeting}/ticket")

    assert response.status_code == 403


def test_a_missing_meeting_gets_no_ticket(client_for, member: User) -> None:
    response = client_for(member).post("/api/audio/live/mtg_missing/ticket")

    assert response.status_code == 404


def test_a_ticket_does_not_buy_another_ticket(
    db_session: Session, bearer_client: TestClient, meeting: str, member: User
) -> None:
    """A ticket that could be traded for a fresh one would live until sign-out."""
    ticket = service.live_ticket(db_session, user=member, meeting_id=meeting)

    response = bearer_client.post(
        f"/api/audio/live/{meeting}/ticket", headers={"Authorization": f"Bearer {ticket}"}
    )

    assert response.status_code == 403


@pytest.mark.parametrize("carried", ["bearer", "cookie"])
def test_a_ticket_opens_no_other_route(
    db_session: Session, bearer_client: TestClient, meeting: str, member: User, carried: str
) -> None:
    """The same request with a session token succeeds, so the refusal is the
    ticket's, not the route's."""
    ticket = service.live_ticket(db_session, user=member, meeting_id=meeting)
    session_token = issue_token(member.id)

    def get(token: str) -> int:
        if carried == "bearer":
            return bearer_client.get(
                f"/api/audio/meetings/{meeting}", headers={"Authorization": f"Bearer {token}"}
            ).status_code
        bearer_client.cookies.set("autune_session", token)
        return bearer_client.get(f"/api/audio/meetings/{meeting}").status_code

    assert get(session_token) == 200
    assert get(ticket) == 403


def test_a_ticket_is_spent_by_its_first_use(
    db_session: Session, meeting: str, member: User
) -> None:
    ticket = service.live_ticket(db_session, user=member, meeting_id=meeting)

    assert service.authenticate_live_ticket(db_session, ticket=ticket, meeting_id=meeting) == member
    with pytest.raises(PermissionDeniedError):
        service.authenticate_live_ticket(db_session, ticket=ticket, meeting_id=meeting)


def test_a_ticket_opens_only_its_own_meeting(
    db_session: Session, team: str, meeting: str, member: User
) -> None:
    from autune_core import Meeting

    other = Meeting(team_id=team, title="Other")
    db_session.add(other)
    db_session.flush()
    ticket = service.live_ticket(db_session, user=member, meeting_id=meeting)

    with pytest.raises(PermissionDeniedError):
        service.authenticate_live_ticket(db_session, ticket=ticket, meeting_id=other.id)
    # Tried once, on the wrong meeting, and spent.
    with pytest.raises(PermissionDeniedError):
        service.authenticate_live_ticket(db_session, ticket=ticket, meeting_id=meeting)


def test_a_ticket_expires(
    db_session: Session, meeting: str, member: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket = service.live_ticket(db_session, user=member, meeting_id=meeting)
    later = tickets.time.monotonic() + tickets.TTL_S + 1
    monkeypatch.setattr(tickets.time, "monotonic", lambda: later)

    with pytest.raises(PermissionDeniedError):
        service.authenticate_live_ticket(db_session, ticket=ticket, meeting_id=meeting)


def test_signing_out_ends_a_ticket(db_session: Session, meeting: str, member: User) -> None:
    """#727: a sign-out after the ticket was issued ends it too."""
    from autune_core.auth import end_sessions

    ticket = service.live_ticket(db_session, user=member, meeting_id=meeting)
    end_sessions(member)
    db_session.flush()

    with pytest.raises(PermissionDeniedError):
        service.authenticate_live_ticket(db_session, ticket=ticket, meeting_id=meeting)
