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

from collections.abc import Callable
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from autune_contracts import MODULES
from autune_core import (
    configure_logging,
    get_logger,
    get_settings,
    load_integration,
    session_scope,
)
from autune_core.integrations_config import teams_with

if TYPE_CHECKING:
    from slack_bolt import App
    from slack_bolt.authorization import AuthorizeResult

configure_logging()
log = get_logger(__name__)

SLACK = "slack"


def register_all(app: App) -> list[str]:
    """Attach every module's handlers. Returns the modules registered."""
    registered = []
    for name in MODULES:
        import_module(f"autune_{name}.slack").register(app)
        log.info("slack_handlers_registered", module=name)
        registered.append(name)
    return registered


def authorize_team(
    enterprise_id: str | None = None, team_id: str | None = None, logger: Any = None
) -> AuthorizeResult | None:
    """The bot token for the Slack workspace a request came from: the one an
    Autune team stored when it installed the app (``team_integrations``, #428).
    Several Autune teams on one workspace hold the same token, so the first
    answers. ``None`` for a workspace no team installed -- Bolt then refuses
    the request.
    """
    from slack_bolt.authorization import AuthorizeResult

    if not team_id:
        return None
    with session_scope() as session:
        for autune_team in teams_with(session, SLACK, "workspace_id", team_id):
            config = load_integration(session, autune_team, SLACK)
            if config is not None and config.secret:
                return AuthorizeResult(
                    enterprise_id=enterprise_id,
                    team_id=team_id,
                    bot_token=config.secret,
                    bot_user_id=str(config.config.get("bot_user_id") or "") or None,
                )
    log.info("slack_request_from_unknown_workspace")
    return None


def build_app() -> App:
    """Construct the Bolt app from configured credentials.

    Raises with an actionable message when the workspace app has not been set
    up yet, rather than Bolt's generic token error.
    """
    from slack_bolt import App

    settings = get_settings()
    if not settings.slack_signing_secret:
        raise RuntimeError(
            "Slack is not configured. Set AUTUNE_SLACK_SIGNING_SECRET in .env "
            "(and AUTUNE_SLACK_BOT_TOKEN for socket mode) — see "
            "docs/engineering/environments.md."
        )

    if settings.slack_bot_token:
        # One workspace, one token: local development in socket mode.
        app = App(token=settings.slack_bot_token, signing_secret=settings.slack_signing_secret)
    else:
        # Every team installs Autune into its own workspace (#428) and stores
        # its own bot token; a request is answered with the token of the
        # workspace it came from (#585).
        # Bolt types ``authorize`` as always answering, but returns a ``None``
        # result as "not authorized" (``CallableAuthorize``) -- what an unknown
        # workspace must get.
        app = App(
            signing_secret=settings.slack_signing_secret,
            authorize=cast("Callable[..., AuthorizeResult]", authorize_team),
        )

    @app.event("app_mention")
    def _acknowledge_mention(body: dict, logger) -> None:  # noqa: ANN001
        """Acknowledge so Slack stops retrying. Modules own the real handlers."""
        logger.debug("mention ignored: %s", body.get("event", {}).get("ts"))

    register_all(app)
    return app
