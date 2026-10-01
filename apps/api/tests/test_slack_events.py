"""Slack's Request URL: a click on a button in Slack reaches the module that
owns it, with the token of the workspace it came from (#585).

No network: the Bolt app is built from settings with a signing secret and no
global token, so it authorizes each request through ``authorize_team``; here
that lookup is stubbed, and Slack's signature is computed the way Slack does.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Iterator
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from slack_bolt.authorization import AuthorizeResult

import autune_bot.app as bot_app
from autune_core.settings import get_settings
from autune_extraction import slack as extraction_slack
from autune_extraction.confirmations import CONFIRM_COMMITMENT

SECRET = "test-signing-secret"


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> Iterator[tuple[TestClient, list[object]]]:
    # Root at DEBUG before the app is built: Bolt copies the root's level into
    # its loggers then, unless the app hands it one.
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("AUTUNE_SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("AUTUNE_SLACK_BOT_TOKEN", "")
    get_settings.cache_clear()
    seen_teams: list[str] = []

    def authorize(enterprise_id=None, team_id=None, logger=None):  # type: ignore[no-untyped-def]
        seen_teams.append(team_id)
        if team_id != "T_INSTALLED":
            return None
        return AuthorizeResult(
            enterprise_id=enterprise_id, team_id=team_id, bot_token="xoxb-t", bot_user_id="U_BOT"
        )

    monkeypatch.setattr(bot_app, "authorize_team", authorize)
    answers: list[object] = []
    monkeypatch.setattr(extraction_slack, "answer_from_slack", answers.append)

    from autune_api.main import create_app

    yield TestClient(create_app()), answers
    get_settings.cache_clear()


def click(team: str) -> str:
    payload = {
        "type": "block_actions",
        "team": {"id": team},
        "user": {"id": "U_SPEAKER", "team_id": team},
        "api_app_id": "A1",
        "token": "unused",
        "trigger_id": "1.2.3",
        "actions": [
            {
                "type": "button",
                "action_id": CONFIRM_COMMITMENT,
                "value": "utt_abc123",
                "block_id": "b1",
                "action_ts": "1.2",
            }
        ],
    }
    return urlencode({"payload": json.dumps(payload)})


def signed(body: str, *, secret: str = SECRET, age: int = 0) -> dict[str, str]:
    stamp = str(int(time.time()) - age)
    digest = hmac.new(secret.encode(), f"v0:{stamp}:{body}".encode(), hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": stamp,
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def test_a_signed_click_reaches_the_module_that_owns_it(
    client: tuple[TestClient, list[object]],
) -> None:
    http, answers = client
    body = click("T_INSTALLED")

    reply = http.post("/api/slack/events", content=body, headers=signed(body))

    assert reply.status_code == 200
    (answer,) = answers
    assert getattr(answer, "utterance_id", None) == "utt_abc123"


def test_a_request_slack_did_not_sign_is_refused(
    client: tuple[TestClient, list[object]],
) -> None:
    http, answers = client
    body = click("T_INSTALLED")

    reply = http.post("/api/slack/events", content=body, headers=signed(body, secret="forged"))

    assert reply.status_code == 401
    assert answers == []


def test_a_refused_request_does_not_log_its_body(
    client: tuple[TestClient, list[object]], caplog: pytest.LogCaptureFixture
) -> None:
    """Bolt logs a request that fails the signature check with its raw body,
    at INFO; a forged copy of a real click quotes a person's line (#610 review)."""
    http, _ = client
    body = click("T_INSTALLED")

    reply = http.post("/api/slack/events", content=body, headers=signed(body, secret="forged"))

    assert reply.status_code == 401
    assert "utt_abc123" not in caplog.text
    assert "U_SPEAKER" not in caplog.text


def test_a_replayed_request_is_refused(client: tuple[TestClient, list[object]]) -> None:
    """Signed by Slack, but older than Slack's five minutes: a replay."""
    http, answers = client
    body = click("T_INSTALLED")

    reply = http.post("/api/slack/events", content=body, headers=signed(body, age=6 * 60))

    assert reply.status_code == 401
    assert answers == []


def test_a_workspace_no_team_installed_gets_no_token(
    client: tuple[TestClient, list[object]],
) -> None:
    http, answers = client
    body = click("T_STRANGER")

    reply = http.post("/api/slack/events", content=body, headers=signed(body))

    # Bolt answers 200 with a "reinstall this app" text rather than an error, so
    # Slack shows the person a reason; what matters is that nothing ran.
    assert reply.status_code == 200
    assert answers == []


def test_without_a_signing_secret_there_is_no_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTUNE_SLACK_SIGNING_SECRET", "")
    get_settings.cache_clear()
    from autune_api.main import create_app

    try:
        paths = {getattr(route, "path", None) for route in create_app().routes}
    finally:
        get_settings.cache_clear()

    assert "/api/slack/events" not in paths
