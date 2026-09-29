"""One-click Slack install (#428) -- without a network, a Redis or a Postgres."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from autune_core import SESSION_COOKIE, issue_token
from autune_core import auth_router as auth_router_module
from autune_core.auth_router import router as auth_router
from autune_core.db import Base, get_session
from autune_core.entities import Meeting, Team, TeamMember, User
from autune_core.errors import AutuneError, PermissionDeniedError
from autune_core.integrations_config import IntegrationConfig
from autune_core.oauth.slack import (
    BOT_SCOPES,
    SlackChannel,
    SlackInstall,
    SlackOAuthClient,
    get_slack_oauth_client,
)
from autune_core.oauth.state import InMemoryStateStore, get_state_store

ME, OUTSIDER, TEAM, MEETING = "user_me", "user_out", "team_1", "mtg_1"


class FakeSlack:
    def __init__(self) -> None:
        self.install: SlackInstall | Exception = SlackInstall(
            "xoxb-1", "U_BOT", "T1", "Acme", "U_ME"
        )
        self.channel: SlackChannel | Exception = SlackChannel("C1", "autune")
        self.made: list[tuple[str, str]] = []
        self.revoked: list[str] = []
        self.usable = True

    def authorization_url(self, *, state: str) -> str:
        return f"https://slack.com/oauth/v2/authorize?state={state}"

    def exchange_code(self, code: str) -> SlackInstall:
        if isinstance(self.install, Exception):
            raise self.install
        return self.install

    def create_alert_channel(self, token: str, name: str, *, invite: str) -> SlackChannel:
        self.made.append((token, invite))
        if isinstance(self.channel, Exception):
            raise self.channel
        return self.channel

    def revoke(self, token: str) -> bool:
        self.revoked.append(token)
        return True

    def channel_usable(self, token: str, channel: str) -> bool:
        return self.usable


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, Team.__table__, TeamMember.__table__, Meeting.__table__],
    )
    db = sessionmaker(bind=engine)()
    db.add(Team(id=TEAM, name="t"))
    for uid in (ME, OUTSIDER):
        db.add(User(id=uid, email=f"{uid}@example.com", display_name=uid))
    db.flush()
    db.add(TeamMember(team_id=TEAM, user_id=ME))
    db.add(Meeting(id=MEETING, team_id=TEAM, title="m"))
    db.commit()

    saved: dict[str, dict[str, Any]] = {}

    def save(_s: Any, team_id: str, service: str, **kw: Any) -> None:
        saved[team_id] = {k: v for k, v in kw.items() if v is not None}

    def load(_s: Any, team_id: str, service: str) -> IntegrationConfig | None:
        row = saved.get(team_id)
        return (
            None
            if row is None
            else IntegrationConfig(service, team_id, row["secret"], row["config"])
        )

    monkeypatch.setattr(auth_router_module, "save_integration", save)
    monkeypatch.setattr(auth_router_module, "load_integration", load)
    monkeypatch.setattr(
        auth_router_module, "disconnect_integration", lambda _s, t, _svc: saved.pop(t)
    )
    monkeypatch.setattr(
        auth_router_module,
        "teams_with",
        lambda _s, _svc, key, value: [
            t for t, row in saved.items() if row["config"].get(key) == value
        ],
    )
    store = InMemoryStateStore()
    slack = FakeSlack()
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")

    @app.exception_handler(AutuneError)
    def _handle(_req: object, exc: AutuneError):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_state_store] = lambda: store
    app.dependency_overrides[get_slack_oauth_client] = lambda: slack
    return {"app": app, "store": store, "slack": slack, "saved": saved}


def signed_in(world: dict[str, Any], user_id: str = ME) -> TestClient:
    client = TestClient(world["app"], follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, issue_token(user_id))
    return client


def start(client: TestClient) -> str:
    response = client.get(
        f"/api/auth/slack/start?meeting_id={MEETING}&redirect_to=/meetings/{MEETING}/actions"
    )
    assert response.status_code == 307, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def test_only_a_member_of_the_meetings_team_can_start(world: dict[str, Any]) -> None:
    response = signed_in(world, OUTSIDER).get(f"/api/auth/slack/start?meeting_id={MEETING}")
    assert response.status_code == 403
    assert world["store"]._entries == {}


def test_installing_stores_the_bot_token_and_the_alert_channel(world: dict[str, Any]) -> None:
    client = signed_in(world)
    response = client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    assert response.headers["location"].endswith(f"/meetings/{MEETING}/actions?slack=connected")
    row = world["saved"][TEAM]
    assert row["secret"] == "xoxb-1"
    assert row["connected_by"] == ME
    # ``channel`` is the key D's briefing and E's report already read.
    assert row["config"]["channel"] == "C1"
    assert row["config"]["channel_name"] == "autune"
    assert row["config"]["workspace_name"] == "Acme"
    # The private channel is made with the installer invited.
    assert world["slack"].made == [("xoxb-1", "U_ME")]
    assert world["slack"].revoked == []


def test_a_callback_from_another_browser_is_refused(world: dict[str, Any]) -> None:
    state = start(signed_in(world))
    other = signed_in(world)  # same person, no state cookie: another browser

    response = other.get(f"/api/auth/slack/callback?state={state}&code=c")

    assert response.status_code == 403
    assert world["saved"] == {}
    assert world["slack"].made == []


def test_a_token_from_an_install_that_then_fails_is_revoked(world: dict[str, Any]) -> None:
    from autune_core.oauth.slack import SlackChannelUnavailableError

    world["slack"].channel = SlackChannelUnavailableError("all taken")
    client = signed_in(world)
    response = client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    assert response.headers["location"].endswith("?slack=failed&reason=slack_channel_unavailable")
    assert world["saved"] == {}
    assert world["slack"].revoked == ["xoxb-1"]


def test_reinstalling_elsewhere_revokes_the_old_workspaces_token(world: dict[str, Any]) -> None:
    client = signed_in(world)
    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")
    world["slack"].install = SlackInstall("xoxb-2", "U_BOT2", "T2", "Other", "U_ME2")
    world["slack"].channel = SlackChannel("C2", "autune")

    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    assert world["saved"][TEAM]["secret"] == "xoxb-2"
    assert world["saved"][TEAM]["config"]["channel"] == "C2"
    assert world["slack"].revoked == ["xoxb-1"]


def test_reinstalling_into_the_same_workspace_keeps_channel_and_token(
    world: dict[str, Any],
) -> None:
    client = signed_in(world)
    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    assert world["saved"][TEAM]["config"]["channel"] == "C1"
    assert world["slack"].made == [("xoxb-1", "U_ME")]  # no second channel
    assert world["slack"].revoked == []  # the same token came back; it stays alive


# --- one workspace, several Autune teams, one bot token (review of #468) -----------


def _other_team_on(world: dict[str, Any], workspace: str, token: str = "xoxb-1") -> None:
    world["saved"]["team_other"] = {
        "secret": token,
        "config": {"channel": "C0", "channel_name": "autune", "workspace_id": workspace},
    }


def test_a_failed_install_leaves_the_token_another_team_uses(world: dict[str, Any]) -> None:
    from autune_core.oauth.slack import SlackChannelUnavailableError

    _other_team_on(world, "T1")
    world["slack"].channel = SlackChannelUnavailableError("all taken")
    client = signed_in(world)

    response = client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    assert "slack=failed" in response.headers["location"]
    assert world["slack"].revoked == []  # team_other still posts with it
    assert TEAM not in world["saved"]


def test_a_failure_that_is_not_ours_still_revokes_then_raises(world: dict[str, Any]) -> None:
    class Broken(FakeSlack):
        def create_alert_channel(self, token: str, name: str, *, invite: str) -> SlackChannel:
            raise RuntimeError("database went away")

    broken = Broken()
    world["app"].dependency_overrides[get_slack_oauth_client] = lambda: broken
    client = signed_in(world)
    state = start(client)

    with pytest.raises(RuntimeError):
        client.get(f"/api/auth/slack/callback?state={state}&code=c")
    assert broken.revoked == ["xoxb-1"]


def test_reinstalling_elsewhere_keeps_a_token_the_old_workspace_still_uses(
    world: dict[str, Any],
) -> None:
    client = signed_in(world)
    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")
    _other_team_on(world, "T1")
    world["slack"].install = SlackInstall("xoxb-2", "U_BOT2", "T2", "Other", "U_ME2")

    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    assert world["saved"][TEAM]["secret"] == "xoxb-2"
    assert world["slack"].revoked == []


def test_disconnecting_leaves_the_bot_for_another_team(world: dict[str, Any]) -> None:
    client = signed_in(world)
    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")
    _other_team_on(world, "T1")

    body = client.post(f"/api/auth/slack/disconnect?meeting_id={MEETING}").json()

    assert body == {"connected": False, "revoked": False, "shared": True}
    assert world["slack"].revoked == []
    assert TEAM not in world["saved"]


def test_a_kept_channel_that_was_archived_is_replaced(world: dict[str, Any]) -> None:
    client = signed_in(world)
    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")
    world["slack"].usable = False
    world["slack"].channel = SlackChannel("C3", "autune-2")

    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    assert world["saved"][TEAM]["config"]["channel"] == "C3"
    assert len(world["slack"].made) == 2


@pytest.mark.parametrize("case", ["declined", "refused"])
def test_a_failed_install_goes_back_to_the_screen(world: dict[str, Any], case: str) -> None:
    client = signed_in(world)
    state = start(client)
    if case == "refused":
        world["slack"].install = PermissionDeniedError("Slack rejected the authorization code")
    extra = "&error=access_denied" if case == "declined" else "&code=c"

    response = client.get(f"/api/auth/slack/callback?state={state}{extra}")

    assert "?slack=failed&reason=" in response.headers["location"]
    assert world["saved"] == {}


def test_status_and_disconnect_revoke_the_token(world: dict[str, Any]) -> None:
    client = signed_in(world)
    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    assert client.get(f"/api/auth/slack?meeting_id={MEETING}").json() == {
        "connected": True,
        "workspace_name": "Acme",
        "channel_name": "autune",
    }
    body = client.post(f"/api/auth/slack/disconnect?meeting_id={MEETING}").json()

    assert body == {"connected": False, "revoked": True, "shared": False}
    assert world["slack"].revoked == ["xoxb-1"]
    assert world["saved"] == {}


# --- the client at its own door --------------------------------------------------------


def _client(answers: dict[str, dict[str, Any]], calls: list[str]) -> SlackOAuthClient:
    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append(method)
        return httpx.Response(200, json=answers[method])

    return SlackOAuthClient(
        client_id="cid",
        client_secret="cs",
        redirect_uri="https://example.test/api/auth/slack/callback",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_the_install_url_asks_for_bot_scopes_only() -> None:
    url = _client({}, []).authorization_url(state="s")
    query = parse_qs(urlsplit(url).query)
    assert query["scope"] == [",".join(BOT_SCOPES)]
    assert "user_scope" not in query
    assert "users:read.email" not in query["scope"][0]
    # Private channel only: nothing that makes or joins a public one.
    assert "channels:manage" not in BOT_SCOPES
    assert "channels:join" not in BOT_SCOPES


def _created(name: str, cid: str) -> dict[str, Any]:
    return {"ok": True, "channel": {"id": cid, "name": name}}


def _slack_at(handler: Any) -> SlackOAuthClient:
    return SlackOAuthClient(
        client_id="cid",
        client_secret="cs",
        redirect_uri="https://example.test/cb",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def _scripted(
    answers: list[dict[str, Any]], sent: list[tuple[str, dict[str, str]]]
) -> SlackOAuthClient:
    replies = iter(answers)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.path.rsplit("/", 1)[-1], _form(request)))
        return httpx.Response(200, json=next(replies))

    return _slack_at(handler)


def test_the_channel_is_private_and_the_installer_is_invited() -> None:
    sent: list[tuple[str, dict[str, str]]] = []
    client = _scripted([_created("autune", "C9"), {"ok": True}], sent)

    assert client.create_alert_channel("xoxb", "autune", invite="U_ME") == SlackChannel(
        "C9", "autune"
    )
    assert sent == [
        ("conversations.create", {"name": "autune", "is_private": "true"}),
        ("conversations.invite", {"channel": "C9", "users": "U_ME"}),
    ]


def test_two_teams_in_one_workspace_get_different_channels() -> None:
    """Review of #468: the second team used to join the first team's channel."""
    workspace: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        form = _form(request)
        if method == "conversations.create":
            if form["name"] in workspace:
                return httpx.Response(200, json={"ok": False, "error": "name_taken"})
            workspace[form["name"]] = f"C{len(workspace) + 1}"
            return httpx.Response(200, json=_created(form["name"], workspace[form["name"]]))
        assert method == "conversations.invite", method  # never join, never list
        return httpx.Response(200, json={"ok": True})

    client = _slack_at(handler)
    team_a = client.create_alert_channel("xoxb", "autune", invite="U_A")
    team_b = client.create_alert_channel("xoxb", "autune", invite="U_B")

    assert team_a == SlackChannel("C1", "autune")
    assert team_b == SlackChannel("C2", "autune-2")


def test_every_name_taken_is_a_named_failure() -> None:
    from autune_core.oauth.slack import NAME_ATTEMPTS, SlackChannelUnavailableError

    sent: list[tuple[str, dict[str, str]]] = []
    client = _scripted([{"ok": False, "error": "name_taken"}] * NAME_ATTEMPTS, sent)

    with pytest.raises(SlackChannelUnavailableError):
        client.create_alert_channel("xoxb", "autune", invite="U_ME")
    assert [m for m, _ in sent] == ["conversations.create"] * NAME_ATTEMPTS


def test_a_channel_nobody_could_be_invited_to_is_archived() -> None:
    sent: list[tuple[str, dict[str, str]]] = []
    client = _scripted(
        [
            _created("autune", "C9"),
            {"ok": False, "error": "user_not_found"},
            {"ok": True},
            {"ok": True},
        ],
        sent,
    )

    with pytest.raises(PermissionDeniedError, match="user_not_found"):
        client.create_alert_channel("xoxb", "autune", invite="U_GONE")
    # Renamed before it is archived, so the failed install does not keep
    # holding #autune and push the next one to #autune-2.
    assert [m for m, _ in sent][-2:] == ["conversations.rename", "conversations.archive"]
    assert sent[-2][1]["name"].startswith("autune-unused-")


def test_an_answer_that_is_not_json_is_our_error_not_a_500() -> None:
    client = _slack_at(lambda request: httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(AutuneError, match="not JSON"):
        client.exchange_code("c")


def test_the_install_carries_the_installer() -> None:
    answer = {
        "ok": True,
        "access_token": "xoxb-1",
        "token_type": "bot",
        "bot_user_id": "U_BOT",
        "team": {"id": "T1", "name": "Acme"},
        "authed_user": {"id": "U_ME"},
    }
    install = _client({"oauth.v2.access": answer}, []).exchange_code("c")
    assert install.installer_id == "U_ME"


def test_a_refused_code_says_why_without_the_code() -> None:
    client = _client({"oauth.v2.access": {"ok": False, "error": "invalid_code"}}, [])
    with pytest.raises(PermissionDeniedError) as caught:
        client.exchange_code("the-secret-code")
    assert "invalid_code" in str(caught.value)
    assert "the-secret-code" not in str(caught.value)


# --- a person's own Slack account, for DMs (#255) --------------------------------------


def test_linking_needs_a_signed_in_person(world: dict[str, Any]) -> None:
    anonymous = TestClient(world["app"], follow_redirects=False)
    assert anonymous.get("/api/auth/slack/me/start").status_code in (401, 403)


class Identifying(FakeSlack):
    def __init__(self, identity: Any) -> None:
        super().__init__()
        self.identity = identity
        self.nonces: list[str] = []

    def identity_url(self, *, state: str, nonce: str) -> str:
        return f"https://slack.com/openid/connect/authorize?state={state}"

    def identify(self, code: str, *, nonce: str) -> Any:
        self.nonces.append(nonce)
        return self.identity


def _link(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    signed_in_as: str,
    workspace: str = "T1",
    installed: bool = True,
    taken_by: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    from autune_core.oauth.slack import SlackIdentity

    if installed:
        world["saved"][TEAM] = {
            "secret": "xoxb-1",
            "config": {"channel": "C1", "workspace_id": "T1", "workspace_name": "Acme"},
        }
    linked: dict[str, Any] = {}
    monkeypatch.setattr(
        auth_router_module,
        "save_user_integration",
        lambda _s, uid, svc, **kw: linked.update(kw, user_id=uid, service=svc),
    )
    monkeypatch.setattr(
        auth_router_module, "users_linked_to_slack_member", lambda _s, member: taken_by or []
    )
    slack = Identifying(SlackIdentity(user_id="U42", team_id=workspace))
    world["app"].dependency_overrides[get_slack_oauth_client] = lambda: slack
    client = signed_in(world, signed_in_as)
    response = client.get("/api/auth/slack/me/start?redirect_to=/meetings/m/actions")
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    back = client.get(f"/api/auth/slack/callback?state={state}&code=c")
    world["identifying"] = slack
    return back.headers["location"], linked


def test_linking_stores_only_the_persons_own_member_id(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    location, linked = _link(world, monkeypatch, signed_in_as=ME)

    assert location.endswith("/meetings/m/actions?slack_me=connected")
    assert linked == {
        "user_id": ME,
        "service": "slack",
        "config": {"slack_user_id": "U42", "slack_team_id": "T1"},
    }
    # The nonce this flow stored is the one identify() is asked to match.
    assert world["identifying"].nonces and world["identifying"].nonces[0]


def test_a_personal_workspace_is_not_linked(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #478: the browser was signed in to another workspace; the
    link would say "linked" while every DM went nowhere."""
    location, linked = _link(world, monkeypatch, signed_in_as=ME, workspace="T_PERSONAL")

    assert location.endswith("?slack_me=failed&reason=slack_wrong_workspace")
    assert linked == {}


def test_linking_before_the_team_installs_says_so(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    location, linked = _link(world, monkeypatch, signed_in_as=ME, installed=False)

    assert location.endswith("?slack_me=failed&reason=slack_team_not_connected")
    assert linked == {}


def test_a_slack_account_linked_to_someone_else_is_refused(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #478: a Slack session left in a shared browser would send this
    person's DMs -- speaking ratio included -- to someone else."""
    location, linked = _link(world, monkeypatch, signed_in_as=ME, taken_by=[OUTSIDER])

    assert location.endswith("?slack_me=failed&reason=slack_account_taken")
    assert linked == {}


def test_relinking_the_same_account_is_fine(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    location, _ = _link(world, monkeypatch, signed_in_as=ME, taken_by=[ME])
    assert location.endswith("?slack_me=connected")


def test_status_names_the_workspace(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from autune_core.user_integrations import UserIntegrationConfig

    _link(world, monkeypatch, signed_in_as=ME)
    monkeypatch.setattr(
        auth_router_module,
        "load_user_integration",
        lambda _s, uid, svc: UserIntegrationConfig(
            svc, uid, None, {"slack_user_id": "U42", "slack_team_id": "T1"}
        ),
    )
    assert signed_in(world).get("/api/auth/slack/me").json() == {
        "linked": True,
        "workspace_name": "Acme",
    }


def _signin_client(answers: list[dict[str, Any]], calls: list[str]) -> SlackOAuthClient:
    replies = iter(answers)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=next(replies))

    return _slack_at(handler)


def _id_token(nonce: str) -> str:
    import jwt

    return jwt.encode(
        {"nonce": nonce, "sub": "U42"}, "signature-not-checked-by-identify-32b", algorithm="HS256"
    )


def test_identify_asks_for_openid_only_checks_the_nonce_and_revokes() -> None:
    calls: list[str] = []
    client = _signin_client(
        [
            {"ok": True, "access_token": "xoxp-once", "id_token": _id_token("n1")},
            {
                "ok": True,
                "sub": "U42",
                "https://slack.com/user_id": "U42",
                "https://slack.com/team_id": "T1",
            },
            {"ok": True, "revoked": True},
        ],
        calls,
    )
    url = client.identity_url(state="s", nonce="n1")
    assert parse_qs(urlsplit(url).query)["scope"] == ["openid"]

    identity = client.identify("c", nonce="n1")

    assert (identity.user_id, identity.team_id) == ("U42", "T1")
    assert calls == ["openid.connect.token", "openid.connect.userInfo", "auth.revoke"]


def test_a_nonce_from_another_flow_is_refused_and_the_token_still_revoked() -> None:
    calls: list[str] = []
    client = _signin_client(
        [
            {"ok": True, "access_token": "xoxp-once", "id_token": _id_token("someone-else")},
            {"ok": True, "revoked": True},
        ],
        calls,
    )
    with pytest.raises(PermissionDeniedError, match="nonce"):
        client.identify("c", nonce="n1")
    assert calls == ["openid.connect.token", "auth.revoke"]
