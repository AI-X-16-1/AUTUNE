"""Each Slack workspace's requests are answered with the token its Autune team
stored when it installed the app (#585)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

import autune_bot.app as bot_app
from autune_core.integrations_config import IntegrationConfig

STORED = {
    "team_a": IntegrationConfig(
        "slack", "team_a", "xoxb-acme", {"workspace_id": "T_ACME", "bot_user_id": "U_BOT"}
    ),
    "team_b": IntegrationConfig("slack", "team_b", "", {"workspace_id": "T_EMPTY"}),
}


@pytest.fixture(autouse=True)
def stored(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def scope() -> Iterator[None]:
        yield None

    monkeypatch.setattr(bot_app, "session_scope", scope)
    monkeypatch.setattr(
        bot_app,
        "teams_with",
        lambda _s, _svc, key, value: [t for t, c in STORED.items() if c.config.get(key) == value],
    )
    monkeypatch.setattr(bot_app, "load_integration", lambda _s, team, _svc: STORED.get(team))


def test_a_workspace_gets_the_token_its_team_stored() -> None:
    result = bot_app.authorize_team(team_id="T_ACME")

    assert result is not None
    assert (result.bot_token, result.bot_user_id, result.team_id) == (
        "xoxb-acme",
        "U_BOT",
        "T_ACME",
    )


@pytest.mark.parametrize("workspace", ["T_STRANGER", "T_EMPTY", None])
def test_no_token_for_a_workspace_without_one(workspace: str | None) -> None:
    assert bot_app.authorize_team(team_id=workspace) is None
