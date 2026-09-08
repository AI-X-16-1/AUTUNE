"""Every module's Slack handlers must register. Nobody edits app.py to add one."""

from __future__ import annotations

import pytest
from slack_bolt import App

from autune_bot import build_app, register_all
from autune_contracts import MODULES


@pytest.fixture
def app() -> App:
    """A Bolt app that needs no credentials, so registration is testable now."""
    return App(
        token="xoxb-test",
        signing_secret="test",
        token_verification_enabled=False,
        request_verification_enabled=False,
    )


def test_every_module_registers(app: App) -> None:
    assert register_all(app) == list(MODULES)


def test_unconfigured_slack_says_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bolt's own error names an env var we do not use; ours names ours."""
    from autune_core import settings as settings_module

    settings_module.get_settings.cache_clear()
    monkeypatch.setenv("AUTUNE_SLACK_BOT_TOKEN", "")
    monkeypatch.setenv("AUTUNE_SLACK_SIGNING_SECRET", "")
    try:
        with pytest.raises(RuntimeError, match="AUTUNE_SLACK_BOT_TOKEN"):
            build_app()
    finally:
        settings_module.get_settings.cache_clear()
