"""Slack Bolt application — assembly only.

Handlers are collected by iterating the module list, exactly as apps/api
collects routers. After W1 nobody edits this file to ship a Slack feature: put
the handler in your module's ``slack.py``.

``register_all`` is separated from ``build_app`` so registration can be tested
without Slack credentials, which do not exist until the workspace app is
created in W3.

See docs/architecture/monorepo.md.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from autune_contracts import MODULES
from autune_core import configure_logging, get_logger, get_settings

if TYPE_CHECKING:
    from slack_bolt import App

configure_logging()
log = get_logger(__name__)


def register_all(app: App) -> list[str]:
    """Attach every module's handlers. Returns the modules registered."""
    registered = []
    for name in MODULES:
        import_module(f"autune_{name}.slack").register(app)
        log.info("slack_handlers_registered", module=name)
        registered.append(name)
    return registered


def build_app() -> App:
    """Construct the Bolt app from configured credentials.

    Raises with an actionable message when the workspace app has not been set
    up yet, rather than Bolt's generic token error.
    """
    from slack_bolt import App

    settings = get_settings()
    if not settings.slack_bot_token or not settings.slack_signing_secret:
        raise RuntimeError(
            "Slack is not configured. Set AUTUNE_SLACK_BOT_TOKEN and "
            "AUTUNE_SLACK_SIGNING_SECRET in .env — see docs/engineering/environments.md."
        )

    app = App(token=settings.slack_bot_token, signing_secret=settings.slack_signing_secret)

    @app.event("app_mention")
    def _acknowledge_mention(body: dict, logger) -> None:  # noqa: ANN001
        """Acknowledge so Slack stops retrying. Modules own the real handlers."""
        logger.debug("mention ignored: %s", body.get("event", {}).get("ts"))

    register_all(app)
    return app
