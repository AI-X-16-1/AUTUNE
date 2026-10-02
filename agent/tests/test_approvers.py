"""``/api/agent/approvers``: who decides a team's L2 proposals, set from the app (#592).

The rule: while a team has no approver, any member may name one; after that
only an approver with scope ``any`` may change the list, and the team never
loses its last ``any`` approver -- otherwise nobody could ever change it again.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_agent import router as routes
from autune_agent.models import AgentApprover
from autune_core import AutuneError, TeamMember, User, current_user, get_session


def _client(session: Session, user_id: str) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/agent")

    @app.exception_handler(AutuneError)
    async def _error(_: object, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: session.get(User, user_id)
    return TestClient(app)


@pytest.fixture
def second(session: Session, team: dict[str, str]) -> str:
    """A second member of the team."""
    user = User(email="second@example.com", display_name="둘째")
    session.add(user)
    session.flush()
    session.add(TeamMember(team_id=team["team"], user_id=user.id))
    session.commit()
    return user.id


def _scopes(session: Session, team_id: str, user_id: str) -> set[str]:
    return set(
        session.scalars(
            select(AgentApprover.scope).where(
                AgentApprover.team_id == team_id, AgentApprover.user_id == user_id
            )
        )
    )


def _grant(session: Session, team_id: str, user_id: str, *scopes: str) -> None:
    session.add_all(AgentApprover(team_id=team_id, user_id=user_id, scope=s) for s in scopes)
    session.commit()


def _set(client: TestClient, team_id: str, user_id: str, scopes: list[str]) -> object:
    return client.put(
        f"/api/agent/approvers/{user_id}", params={"team_id": team_id}, json={"scopes": scopes}
    )


def test_a_new_team_lists_every_member_and_lets_anyone_manage(
    session: Session, team: dict[str, str], second: str
) -> None:
    body = (
        _client(session, team["member"])
        .get("/api/agent/approvers", params={"team_id": team["team"]})
        .json()
    )

    assert body["can_manage"] is True
    assert body["scopes"] == ["any", "report", "research", "followup", "workload"]
    assert {m["user_id"]: m["scopes"] for m in body["members"]} == {
        team["member"]: [],
        second: [],
    }
    assert {m["name"] for m in body["members"]} == {"팀원", "둘째"}


def test_the_first_approver_named_must_hold_any(session: Session, team: dict[str, str]) -> None:
    client = _client(session, team["member"])

    refused = _set(client, team["team"], team["member"], ["report"])
    named = _set(client, team["team"], team["member"], ["any"])

    assert refused.status_code == 409
    assert named.status_code == 200
    assert _scopes(session, team["team"], team["member"]) == {"any"}


def test_once_named_only_an_any_approver_manages(
    session: Session, team: dict[str, str], second: str
) -> None:
    _grant(session, team["team"], team["member"], "any")

    by_second = _client(session, second)
    listing = by_second.get("/api/agent/approvers", params={"team_id": team["team"]}).json()
    refused = _set(by_second, team["team"], second, ["any"])
    granted = _set(_client(session, team["member"]), team["team"], second, ["report", "workload"])

    assert listing["can_manage"] is False
    assert refused.status_code == 403
    assert granted.status_code == 200
    assert _scopes(session, team["team"], second) == {"report", "workload"}


def test_a_scoped_approver_cannot_manage(
    session: Session, team: dict[str, str], second: str
) -> None:
    _grant(session, team["team"], team["member"], "any")
    _grant(session, team["team"], second, "report")

    reply = _set(_client(session, second), team["team"], second, ["report", "research"])

    assert reply.status_code == 403
    assert _scopes(session, team["team"], second) == {"report"}


def test_setting_replaces_the_member_scopes(
    session: Session, team: dict[str, str], second: str
) -> None:
    _grant(session, team["team"], team["member"], "any")
    _grant(session, team["team"], second, "report", "research")
    client = _client(session, team["member"])

    _set(client, team["team"], second, ["workload"])
    _set(client, team["team"], second, [])

    assert _scopes(session, team["team"], second) == set()


def test_the_last_any_approver_cannot_be_removed(
    session: Session, team: dict[str, str], second: str
) -> None:
    _grant(session, team["team"], team["member"], "any")
    client = _client(session, team["member"])

    alone = _set(client, team["team"], team["member"], ["report"])
    _set(client, team["team"], second, ["any"])
    handed_over = _set(client, team["team"], team["member"], ["report"])

    assert alone.status_code == 409
    assert handed_over.status_code == 200
    assert _scopes(session, team["team"], team["member"]) == {"report"}
    assert _scopes(session, team["team"], second) == {"any"}


def test_an_approver_row_of_a_former_member_does_not_count(
    session: Session, team: dict[str, str]
) -> None:
    # The outsider once held ``any`` and left; the team is unmanaged again.
    _grant(session, team["team"], team["outsider"], "any")

    body = (
        _client(session, team["member"])
        .get("/api/agent/approvers", params={"team_id": team["team"]})
        .json()
    )

    assert body["can_manage"] is True
    assert team["outsider"] not in {m["user_id"] for m in body["members"]}


def test_a_team_whose_any_approver_left_opens_again_despite_narrower_rows(
    session: Session, team: dict[str, str]
) -> None:
    """The ``any`` approver left; a member who keeps ``report`` must not lock the list."""
    _grant(session, team["team"], team["outsider"], "any")
    _grant(session, team["team"], team["member"], "report")
    client = _client(session, team["member"])

    body = client.get("/api/agent/approvers", params={"team_id": team["team"]}).json()
    took_over = _set(client, team["team"], team["member"], ["any", "report"])

    assert body["can_manage"] is True
    assert took_over.status_code == 200
    assert _scopes(session, team["team"], team["member"]) == {"any", "report"}


def test_a_non_member_is_refused_and_cannot_be_named(
    session: Session, team: dict[str, str]
) -> None:
    outsider = _client(session, team["outsider"])
    member = _client(session, team["member"])

    listing = outsider.get("/api/agent/approvers", params={"team_id": team["team"]})
    naming_self = _set(outsider, team["team"], team["outsider"], ["any"])
    named_by_member = _set(member, team["team"], team["outsider"], ["any"])

    assert listing.status_code == 403
    assert naming_self.status_code == 403
    assert named_by_member.status_code == 404
    assert _scopes(session, team["team"], team["outsider"]) == set()


def test_an_unknown_scope_is_422(session: Session, team: dict[str, str]) -> None:
    reply = _set(_client(session, team["member"]), team["team"], team["member"], ["admin"])

    assert reply.status_code == 422
