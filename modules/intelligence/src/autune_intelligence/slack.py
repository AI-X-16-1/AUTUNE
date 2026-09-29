"""Slack handlers for module E.

Collected automatically by apps/bot. Handlers acknowledge and delegate to
``service`` — no business logic here, the same rule as router.py and tasks.py.

Surface: weekly report, meeting report, prediction warnings, personal
speaking-ratio DM.
See docs/modules/intelligence.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from autune_core import get_logger

from .service import MEETING_REPORT_OPEN_ACTION, MEETING_REPORT_REVIEW_ACTION

if TYPE_CHECKING:
    from slack_bolt import App

log = get_logger(__name__)


def register(app: App) -> None:
    """Attach this module's Slack handlers.

    E's Slack surface is outbound: weekly report, meeting report, prediction
    warnings, and the speaking-ratio DM (see docs/modules/intelligence.md).
    The interactive components are the meeting report's two URL buttons,
    "상세보기" and "확인하러 가기". The browser opens the page, but Slack still
    sends the click to the app and shows an error unless it is acknowledged, so
    the handlers only acknowledge. feedback.py's DM has no buttons on purpose (a
    working opt-out needs a handler plus stored state, a separate change).

    The speaking-ratio DM goes through SlackClient.send_personal, which
    refuses any recipient but the subject and refuses a channel outright. See
    docs/architecture/privacy.md section 3.

    A module with nothing to register leaves this as a no-op; apps/bot calls it
    either way so no one has to edit the app to add a handler later.
    """
    app.action(MEETING_REPORT_OPEN_ACTION)(_ack_only)
    app.action(MEETING_REPORT_REVIEW_ACTION)(_ack_only)


def _ack_only(ack: Any) -> None:
    ack()
