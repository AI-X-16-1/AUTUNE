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
        self.names: list[tuple[str, str]] = []
        self.revoked: list[str] = []
        self.calls: list[str] = []
        self.discarded: list[tuple[str, str]] = []
        self.exchanged = 0
        self.usable = True

    def authorization_url(self, *, state: str) -> str:
        return f"https://slack.com/oauth/v2/authorize?state={state}"

    def exchange_code(self, code: str) -> SlackInstall:
        self.exchanged += 1
        if isinstance(self.install, Exception):
            raise self.install
        return self.install

    def create_alert_channel(
        self, token: str, name: str, *, invite: str, fallback: str = ""
    ) -> SlackChannel:
        self.made.append((token, invite))
        self.names.append((name, fallback))
        if isinstance(self.channel, Exception):
            raise self.channel
        return self.channel

    def revoke(self, token: str) -> bool:
        self.calls.append("revoke")
        self.revoked.append(token)
        return True

    def discard_channel(self, token: str, channel: str) -> None:
        self.calls.append("discard")
        self.discarded.append((token, channel))

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
    # This test database stores no encrypted secret; the check is its own test.
    monkeypatch.setattr(auth_router_module, "ensure_configured", lambda: None)
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


def test_a_deploy_that_cannot_store_the_token_fails_before_slack_is_touched(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#593: an empty AUTUNE_ENCRYPTION_KEY used to fail in save_integration,
    after the channel was made -- and each retry made the next #autune-N."""
    from autune_core.errors import ConfigurationError

    def unset() -> None:
        raise ConfigurationError("AUTUNE_ENCRYPTION_KEY is not set")

    client = signed_in(world)
    state = start(client)  # the key went between start and callback
    monkeypatch.setattr(auth_router_module, "ensure_configured", unset)
    response = client.get(f"/api/auth/slack/callback?state={state}&code=c")

    assert response.headers["location"].endswith("?slack=failed&reason=configuration_error")
    assert world["slack"].exchanged == 0, "no token was issued"
    assert (world["slack"].made, world["slack"].revoked) == ([], [])


def test_a_deploy_that_cannot_store_the_token_sends_nobody_to_slack(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """mkkim68, review of #765: no install screen that cannot succeed."""
    from autune_core.errors import ConfigurationError

    def unset() -> None:
        raise ConfigurationError("AUTUNE_ENCRYPTION_KEY is not set")

    monkeypatch.setattr(auth_router_module, "ensure_configured", unset)
    response = signed_in(world).get(f"/api/auth/slack/start?meeting_id={MEETING}")

    assert response.status_code == 303
    assert response.headers["location"].endswith("?slack=failed&reason=configuration_error")
    assert world["store"]._entries == {}


def test_a_channel_made_by_an_install_that_then_fails_is_put_away_first(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#593: archived with the new token, before that token is revoked."""
    from autune_core.errors import ConfigurationError

    def refused(*_: Any, **__: Any) -> None:
        raise ConfigurationError("the row could not be written")

    monkeypatch.setattr(auth_router_module, "save_integration", refused)
    client = signed_in(world)
    response = client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    assert "slack=failed" in response.headers["location"]
    assert world["slack"].discarded == [("xoxb-1", "C1")]
    assert world["slack"].calls == ["discard", "revoke"]


def test_a_kept_channel_is_not_put_away_when_a_reinstall_fails(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from autune_core.errors import ConfigurationError

    client = signed_in(world)
    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")  # installed, C1
    world["slack"].install = SlackInstall("xoxb-2", "U_BOT", "T1", "Acme", "U_ME")

    def refused(*_: Any, **__: Any) -> None:
        raise ConfigurationError("the row could not be written")

    monkeypatch.setattr(auth_router_module, "save_integration", refused)
    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    assert world["slack"].discarded == [], "C1 is the team's channel, kept on a re-install"


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
        def create_alert_channel(
            self, token: str, name: str, *, invite: str, fallback: str = ""
        ) -> SlackChannel:
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
        "channel_url": "https://app.slack.com/client/T1/C1",
    }
    body = client.post(f"/api/auth/slack/disconnect?meeting_id={MEETING}").json()

    assert body == {"connected": False, "revoked": True, "shared": False}
    assert world["slack"].revoked == ["xoxb-1"]
    assert world["saved"] == {}


def test_settings_names_the_team_instead_of_a_meeting(world: dict[str, Any]) -> None:
    """S28 (#496): the same routes, the team named directly, the same member check."""
    client = signed_in(world)
    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    assert client.get(f"/api/auth/slack?team_id={TEAM}").json()["connected"] is True
    response = client.get(
        f"/api/auth/slack/start?team_id={TEAM}&redirect_to=/settings/integrations"
    )
    assert response.status_code == 307


def test_a_team_the_person_is_not_on_is_refused(world: dict[str, Any]) -> None:
    response = signed_in(world, OUTSIDER).get(f"/api/auth/slack?team_id={TEAM}")

    assert response.status_code == 403


def test_a_scope_is_required(world: dict[str, Any]) -> None:
    assert signed_in(world).get("/api/auth/slack").status_code == 422


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
        self.dms: list[tuple[str, str, str]] = []

    def identity_url(self, *, state: str, nonce: str) -> str:
        return f"https://slack.com/openid/connect/authorize?state={state}"

    def identify(self, code: str, *, nonce: str) -> Any:
        self.nonces.append(nonce)
        return self.identity

    def send_link_confirmation(self, token: str, member_id: str, text: str) -> None:
        self.dms.append((token, member_id, text))


def _link(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    signed_in_as: str,
    workspace: str = "T1",
    installed: bool = True,
    taken_by: list[str] | None = None,
    member: str = "U42",
) -> tuple[str, dict[str, Any]]:
    """Start and finish a link as ``signed_in_as``. Returns where the callback
    sent the browser and the people's stored Slack configs, by user id."""
    from autune_core.oauth.slack import SlackIdentity
    from autune_core.user_integrations import UserIntegrationConfig

    if installed:
        world["saved"][TEAM] = {
            "secret": "xoxb-1",
            "config": {"channel": "C1", "workspace_id": "T1", "workspace_name": "Acme"},
        }
    people: dict[str, Any] = world.setdefault("people", {})

    def save(_s: Any, uid: str, svc: str, *, config: dict[str, Any], **_: Any) -> None:
        assert svc == "slack"
        people[uid] = dict(config)

    def load(_s: Any, uid: str, svc: str) -> UserIntegrationConfig | None:
        return None if uid not in people else UserIntegrationConfig(svc, uid, None, people[uid])

    monkeypatch.setattr(auth_router_module, "save_user_integration", save)
    monkeypatch.setattr(auth_router_module, "load_user_integration", load)
    monkeypatch.setattr(
        auth_router_module,
        "users_linked_to_slack_member",
        lambda _s, m: (
            (taken_by or []) + [u for u, c in people.items() if c.get("slack_user_id") == m]
        ),
    )
    slack = Identifying(SlackIdentity(user_id=member, team_id=workspace))
    world["app"].dependency_overrides[get_slack_oauth_client] = lambda: slack
    client = signed_in(world, signed_in_as)
    response = client.get("/api/auth/slack/me/start?redirect_to=/meetings/m/actions")
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    back = client.get(f"/api/auth/slack/callback?state={state}&code=c")
    world["identifying"] = slack
    return back.headers["location"], people


def _confirm_link(world: dict[str, Any]) -> str:
    """The confirmation link from the bot's DM, as a path this app serves."""
    (_, _, text) = world["identifying"].dms[-1]
    url = next(word for word in text.split() if "/slack/me/confirm" in word)
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}"


def test_linking_waits_for_the_slack_account_to_confirm(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#478 review: the browser may hold someone else's Slack session, so the
    link is pending until that account confirms it -- and no DM goes there."""
    location, people = _link(world, monkeypatch, signed_in_as=ME)

    assert location.endswith("/meetings/m/actions?slack_me=pending")
    stored = people[ME]
    assert "slack_user_id" not in stored, "nothing may be sent to it yet"
    assert (stored["pending_slack_user_id"], stored["pending_slack_team_id"]) == ("U42", "T1")
    ((bot, member, text),) = world["identifying"].dms
    assert (bot, member) == ("xoxb-1", "U42")
    token = parse_qs(urlsplit(_confirm_link(world)).query)["token"][0]
    assert token not in str(stored), "only a digest of the link is kept"
    assert world["identifying"].nonces and world["identifying"].nonces[0]


def test_the_confirmation_link_is_on_the_web_origin(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#478 review: built from the Host the API saw, the link pointed at the
    proxy's target -- unreachable, or without the session cookie."""
    from autune_core.settings import get_settings

    _link(world, monkeypatch, signed_in_as=ME)
    (_, _, text) = world["identifying"].dms[-1]
    url = next(word for word in text.split() if "/slack/me/confirm" in word)

    assert url.startswith(
        get_settings().web_base_url.rstrip("/") + "/api/auth/slack/me/confirm?token="
    )
    assert "testserver" not in url


def test_the_link_confirms_in_the_session_that_started_it(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, people = _link(world, monkeypatch, signed_in_as=ME)

    back = signed_in(world, ME).get(_confirm_link(world))

    assert back.headers["location"].endswith("/meetings/m/actions?slack_me=connected")
    assert people[ME] == {"slack_user_id": "U42", "slack_team_id": "T1"}


def test_a_leftover_slack_sessions_owner_cannot_confirm_it(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The DM reaches the owner of the Slack session left in the browser. Opening
    it in their own Autune session links nothing -- to them or to the starter."""
    _, people = _link(world, monkeypatch, signed_in_as=ME)
    before = dict(people[ME])

    back = signed_in(world, OUTSIDER).get(_confirm_link(world))

    assert back.headers["location"].endswith("slack_me=failed&reason=slack_link_not_confirmed")
    assert people[ME] == before
    assert OUTSIDER not in people


def test_a_confirmation_link_works_once(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _link(world, monkeypatch, signed_in_as=ME)
    link = _confirm_link(world)
    me = signed_in(world, ME)
    me.get(link)

    again = me.get(link)

    assert again.headers["location"].endswith("reason=slack_link_not_confirmed")


def test_a_wrong_or_late_link_confirms_nothing(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, people = _link(world, monkeypatch, signed_in_as=ME)
    me = signed_in(world, ME)

    wrong = me.get("/api/auth/slack/me/confirm?token=guessed")
    people[ME]["confirm_expires_at"] = "2020-01-01T00:00:00+00:00"
    late = me.get(_confirm_link(world))

    assert wrong.headers["location"].endswith("reason=slack_link_not_confirmed")
    assert late.headers["location"].endswith("reason=slack_link_not_confirmed")
    assert "slack_user_id" not in people[ME]


def test_a_browser_without_a_session_is_told_where_to_open_the_link(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slack's app opens links in the default browser, often not the one
    signed in to Autune. That browser gets a page saying what to do -- not a
    JSON 403 -- and the link stays usable where it belongs."""
    _, people = _link(world, monkeypatch, signed_in_as=ME)
    anonymous = TestClient(world["app"], follow_redirects=False)

    page = anonymous.get(_confirm_link(world))

    assert page.status_code == 401
    assert page.headers["content-type"].startswith("text/html")
    assert "Autune에 로그인한 상태로" in page.text
    assert "slack_user_id" not in people[ME]
    back = signed_in(world, ME).get(_confirm_link(world))
    assert back.headers["location"].endswith("slack_me=connected")


def test_a_personal_workspace_is_not_linked(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #478: the browser was signed in to another workspace; the
    link would say "linked" while every DM went nowhere."""
    location, people = _link(world, monkeypatch, signed_in_as=ME, workspace="T_PERSONAL")

    assert location.endswith("?slack_me=failed&reason=slack_wrong_workspace")
    assert people == {}
    assert world["identifying"].dms == []


def test_linking_before_the_team_installs_says_so(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    location, people = _link(world, monkeypatch, signed_in_as=ME, installed=False)

    assert location.endswith("?slack_me=failed&reason=slack_team_not_connected")
    assert people == {}


def test_a_slack_account_linked_to_someone_else_is_refused(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of #478: a Slack session left in a shared browser would send this
    person's DMs -- speaking ratio included -- to someone else."""
    location, people = _link(world, monkeypatch, signed_in_as=ME, taken_by=[OUTSIDER])

    assert location.endswith("?slack_me=failed&reason=slack_account_taken")
    assert people == {}
    assert world["identifying"].dms == [], "no confirmation DM to an account already taken"


def test_relinking_keeps_the_confirmed_account_while_the_new_one_waits(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    world["people"] = {ME: {"slack_user_id": "U42", "slack_team_id": "T1"}}

    location, people = _link(world, monkeypatch, signed_in_as=ME, member="U77")

    assert location.endswith("?slack_me=pending")
    assert people[ME]["slack_user_id"] == "U42"
    assert people[ME]["pending_slack_user_id"] == "U77"


def test_status_says_linked_only_once_confirmed(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _link(world, monkeypatch, signed_in_as=ME)
    me = signed_in(world, ME)

    assert me.get("/api/auth/slack/me").json() == {"linked": False, "pending": True}
    me.get(_confirm_link(world))
    assert me.get("/api/auth/slack/me").json() == {"linked": True, "workspace_name": "Acme"}


def test_the_confirmation_dm_is_one_chat_postmessage_to_the_member() -> None:
    calls: list[tuple[str, dict[str, str], str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(parse_qs(request.content.decode()))
        calls.append(
            (
                request.url.path.rsplit("/", 1)[-1],
                {k: v[0] for k, v in body.items()},
                request.headers["authorization"],
            )
        )
        return httpx.Response(200, json={"ok": True})

    _slack_at(handler).send_link_confirmation("xoxb-1", "U42", "링크")

    assert calls == [("chat.postMessage", {"channel": "U42", "text": "링크"}, "Bearer xoxb-1")]


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


def test_a_late_link_is_not_pending_any_more(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past its 30 minutes the screen should offer to start again, not "check
    your DM" (mkkim68, review of #478)."""
    _, people = _link(world, monkeypatch, signed_in_as=ME)
    people[ME]["confirm_expires_at"] = "2020-01-01T00:00:00+00:00"

    status = signed_in(world, ME).get("/api/auth/slack/me").json()

    assert status == {"linked": False, "pending": False}


def test_two_confirmations_racing_for_one_account_leave_one_link(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both pass the route's check; the unique index refuses the second, and the
    person is told the account is taken rather than shown a 500."""
    from sqlalchemy.exc import IntegrityError

    _, people = _link(world, monkeypatch, signed_in_as=ME)
    before = dict(people[ME])
    link = _confirm_link(world)

    def refused(*_: Any, **__: Any) -> None:
        raise IntegrityError("INSERT", {}, Exception("uq_user_integrations_slack_member"))

    monkeypatch.setattr(auth_router_module, "save_user_integration", refused)
    back = signed_in(world, ME).get(link)

    assert back.headers["location"].endswith("slack_me=failed&reason=slack_account_taken")
    assert people[ME] == before


# --- the channel is named after the team (the user, 2026-10-04) -------------------


def test_the_install_names_the_channel_after_the_team(world: dict[str, Any]) -> None:
    client = signed_in(world)
    client.get(f"/api/auth/slack/callback?state={start(client)}&code=c")

    # The fixture's team is called "t"; the setting is the fallback.
    assert world["slack"].names == [("t", "autune")]


@pytest.mark.parametrize(
    ("team", "channel"),
    [
        ("제품팀", "제품팀"),
        ("Growth Squad", "growth-squad"),
        ("  AI-X 16기 / 1조! ", "ai-x-16기-1조"),
        ("Data_Team", "data_team"),
        ("!!!", "autune"),
        ("", "autune"),
    ],
)
def test_a_team_name_becomes_a_channel_name(team: str, channel: str) -> None:
    from autune_core.oauth.slack import channel_name_for

    assert channel_name_for(team, "autune") == channel


def test_a_long_team_name_leaves_room_for_a_number() -> None:
    from autune_core.oauth.slack import MAX_CHANNEL_NAME, channel_name_for

    name = channel_name_for("가" * 200, "autune")

    assert len(name) == MAX_CHANNEL_NAME and len(f"{name}-10") <= 80


def test_a_name_slack_will_not_take_falls_back() -> None:
    sent: list[tuple[str, dict[str, str]]] = []
    client = _scripted(
        [{"ok": False, "error": "invalid_name_specials"}, _created("autune", "C9"), {"ok": True}],
        sent,
    )

    channel = client.create_alert_channel("xoxb", "제품팀", invite="U_ME", fallback="autune")

    assert channel == SlackChannel("C9", "autune")
    assert [form.get("name") for m, form in sent if m == "conversations.create"] == [
        "제품팀",
        "autune",
    ]


def test_without_a_fallback_a_refused_name_is_still_a_refusal() -> None:
    from autune_core.errors import PermissionDeniedError

    sent: list[tuple[str, dict[str, str]]] = []
    client = _scripted([{"ok": False, "error": "invalid_name"}], sent)

    with pytest.raises(PermissionDeniedError):
        client.create_alert_channel("xoxb", "autune", invite="U_ME")
