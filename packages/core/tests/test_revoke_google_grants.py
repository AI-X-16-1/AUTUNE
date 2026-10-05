"""A deleted account's Google grants are revoked at Google (#763), after every
module's user hook and without ever stopping the deletion -- without a
network or a Postgres."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from autune_core import db as db_module
from autune_core import deletion
from autune_core import user_integrations as ui
from autune_core.oauth import google as google_module
from autune_core.oauth.google import REVOKE_ENDPOINT, revoke_token
from autune_core.user_integrations import UserIntegrationConfig

USER = "usr_1"


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    grants: dict[str, UserIntegrationConfig] = {}
    revoked: list[str] = []
    answer = {"ok": True}

    @contextmanager
    def scope() -> Iterator[object]:
        yield object()

    def load(_s: object, user_id: str, service: str) -> UserIntegrationConfig | None:
        grant = grants.get(service)
        return grant if grant is not None and grant.user_id == user_id else None

    def revoke(token: str, **_: Any) -> bool:
        revoked.append(token)
        return answer["ok"]

    monkeypatch.setattr(db_module, "session_scope", scope)
    monkeypatch.setattr(ui, "load_user_integration", load)
    monkeypatch.setattr(google_module, "revoke_token", revoke)
    monkeypatch.setattr(deletion, "_user_hooks", {})
    return {"grants": grants, "revoked": revoked, "answer": answer}


def calendar(secret: str | None = "1//refresh", **config: Any) -> UserIntegrationConfig:
    return UserIntegrationConfig("calendar", USER, secret, config)


def test_a_calendar_grant_is_revoked(world: dict[str, Any]) -> None:
    world["grants"]["calendar"] = calendar(google_sub="sub-1", client_id="cid")

    ui.revoke_google_grants(USER)

    assert world["revoked"] == ["1//refresh"]


def test_a_gmail_grant_is_revoked_with_the_account(world: dict[str, Any]) -> None:
    """A grant to send mail as the person must not outlive their account at
    Google (#760 review): with the real ``GOOGLE_SERVICES``, not a stand-in."""
    world["grants"]["gmail_send"] = UserIntegrationConfig(
        "gmail_send", USER, "1//mail", {"google_sub": "sub-2", "client_id": "cid"}
    )
    world["grants"]["calendar"] = calendar(google_sub="sub-1", client_id="cid")

    ui.revoke_google_grants(USER)

    assert world["revoked"] == ["1//refresh", "1//mail"]


def test_nothing_connected_asks_google_nothing(world: dict[str, Any]) -> None:
    ui.revoke_google_grants(USER)
    assert world["revoked"] == []


def test_a_row_without_a_secret_asks_google_nothing(world: dict[str, Any]) -> None:
    world["grants"]["calendar"] = calendar(secret=None)
    ui.revoke_google_grants(USER)
    assert world["revoked"] == []


def test_two_grants_of_one_account_and_client_are_revoked_once(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One revoke ends everything that account gave that client; a second
    service on the same grant (#760's ``gmail_send``) is already gone."""
    monkeypatch.setattr(ui, "GOOGLE_SERVICES", ("calendar", "other"))
    world["grants"]["calendar"] = calendar("1//a", google_sub="sub-1", client_id="cid")
    world["grants"]["other"] = UserIntegrationConfig(
        "other", USER, "1//b", {"google_sub": "sub-1", "client_id": "cid"}
    )

    ui.revoke_google_grants(USER)

    assert world["revoked"] == ["1//a"]


def test_grants_of_different_accounts_are_each_revoked(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui, "GOOGLE_SERVICES", ("calendar", "other"))
    world["grants"]["calendar"] = calendar("1//a", google_sub="sub-1", client_id="cid")
    world["grants"]["other"] = UserIntegrationConfig(
        "other", USER, "1//b", {"google_sub": "sub-2", "client_id": "cid"}
    )

    ui.revoke_google_grants(USER)

    assert world["revoked"] == ["1//a", "1//b"]


def test_a_refused_first_revoke_does_not_skip_the_second(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui, "GOOGLE_SERVICES", ("calendar", "other"))
    world["answer"]["ok"] = False
    world["grants"]["calendar"] = calendar("1//a", google_sub="sub-1", client_id="cid")
    world["grants"]["other"] = UserIntegrationConfig(
        "other", USER, "1//b", {"google_sub": "sub-1", "client_id": "cid"}
    )

    ui.revoke_google_grants(USER)

    assert world["revoked"] == ["1//a", "1//b"]


def test_a_refusal_is_logged_without_the_token_and_does_not_raise(
    world: dict[str, Any],
) -> None:
    world["answer"]["ok"] = False
    world["grants"]["calendar"] = calendar()

    with capture_logs() as logs:
        ui.revoke_google_grants(USER)

    (entry,) = [e for e in logs if e["event"] == "user_google_grant_not_revoked"]
    assert entry["service"] == "calendar"
    assert "1//refresh" not in repr(logs)


def test_an_unreadable_grant_is_logged_and_does_not_raise(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key that no longer decrypts the row, say: the deletion goes on."""
    from autune_core.errors import ConfigurationError

    def broken(*_: Any) -> None:
        raise ConfigurationError("could not decrypt")

    monkeypatch.setattr(ui, "load_user_integration", broken)

    with capture_logs() as logs:
        ui.revoke_google_grants(USER)

    # Once per service -- the calendar's row and the Gmail one (#760).
    assert [(e["event"], e["service"]) for e in logs] == [
        ("user_google_grant_unreadable", service) for service in ui.GOOGLE_SERVICES
    ]
    assert world["revoked"] == []


def test_one_unreadable_grant_does_not_skip_the_others(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from autune_core.errors import ConfigurationError

    monkeypatch.setattr(ui, "GOOGLE_SERVICES", ("broken", "calendar"))
    world["grants"]["calendar"] = calendar()
    real = ui.load_user_integration

    def load(session: object, user_id: str, service: str) -> UserIntegrationConfig | None:
        if service == "broken":
            raise ConfigurationError("could not decrypt")
        return real(session, user_id, service)

    monkeypatch.setattr(ui, "load_user_integration", load)

    ui.revoke_google_grants(USER)

    assert world["revoked"] == ["1//refresh"]


def test_anything_a_revoke_raises_does_not_stop_the_deletion(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """mkkim68, review of #766: not only ``httpx.HTTPError``."""
    monkeypatch.setattr(ui, "GOOGLE_SERVICES", ("calendar", "other"))
    world["grants"]["calendar"] = calendar("1//a")
    world["grants"]["other"] = UserIntegrationConfig("other", USER, "1//b", {})
    tried: list[str] = []

    def explode(token: str, **_: Any) -> bool:
        tried.append(token)
        if token == "1//a":
            raise RuntimeError("something nobody expected")
        return True

    monkeypatch.setattr(google_module, "revoke_token", explode)

    with capture_logs() as logs:
        ui.revoke_google_grants(USER)

    assert tried == ["1//a", "1//b"]
    assert "1//a" not in repr(logs)
    assert "something nobody expected" not in repr(logs)


def test_the_revoke_runs_after_every_module_hook(world: dict[str, Any]) -> None:
    """B removes the calendar's due dates with the grant; it must still work."""
    order: list[str] = []
    world["grants"]["calendar"] = calendar()
    deletion.on_user_deleted("extraction")(lambda uid: order.append(f"hook:{world['revoked']}"))

    deletion.run_user_hooks(USER)

    assert order == ["hook:[]"]
    assert world["revoked"] == ["1//refresh"]


def test_a_failing_hook_leaves_the_grant_alone(world: dict[str, Any]) -> None:
    """The deletion stops and the account stays, so its grant must still work."""
    world["grants"]["calendar"] = calendar()

    def broken(_uid: str) -> None:
        raise RuntimeError("could not clean up")

    deletion.on_user_deleted("extraction")(broken)

    with pytest.raises(RuntimeError):
        deletion.run_user_hooks(USER)
    assert world["revoked"] == []


# --- revoke_token, at its own door -----------------------------------------------------


def test_revoke_token_posts_the_token_alone() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    assert revoke_token("1//r", http=httpx.Client(transport=httpx.MockTransport(handler)))
    (request,) = seen
    assert str(request.url) == REVOKE_ENDPOINT
    assert request.content == b"token=1%2F%2Fr"


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(lambda _r: httpx.Response(400, json={"error": "invalid_token"}), id="refused"),
        pytest.param(lambda _r: (_ for _ in ()).throw(httpx.ConnectError("down")), id="down"),
    ],
)
def test_revoke_token_answers_false_rather_than_raising(handler: Any) -> None:
    assert not revoke_token("1//r", http=httpx.Client(transport=httpx.MockTransport(handler)))
