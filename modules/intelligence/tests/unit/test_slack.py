"""E's Slack handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

from autune_intelligence import slack
from autune_intelligence.service import MEETING_REPORT_OPEN_ACTION


class _App:
    """Records ``app.action(action_id)(handler)`` the way slack_bolt registers it."""

    def __init__(self) -> None:
        self.actions: dict[str, Callable[..., Any]] = {}

    def action(self, action_id: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def attach(handler: Callable[..., Any]) -> Callable[..., Any]:
            self.actions[action_id] = handler
            return handler

        return attach


def test_the_report_details_button_is_acknowledged() -> None:
    """A URL button still sends an action; unacknowledged, Slack shows an error."""
    app = _App()
    slack.register(app)  # type: ignore[arg-type]
    ack = MagicMock()

    app.actions[MEETING_REPORT_OPEN_ACTION](ack=ack)

    ack.assert_called_once_with()
