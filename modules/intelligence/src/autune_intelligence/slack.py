"""Slack handlers for module E.

Collected automatically by apps/bot. Handlers acknowledge and delegate to
``service`` — no business logic here, the same rule as router.py and tasks.py.

Surface: weekly report, prediction warnings, personal speaking-ratio DM.
See docs/modules/intelligence.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from autune_core import get_logger

if TYPE_CHECKING:
    from slack_bolt import App

log = get_logger(__name__)


def register(app: App) -> None:
    """Attach this module's Slack handlers.

    E's Slack surface is outbound-only right now: weekly report, prediction
    warnings, and the speaking-ratio DM (see docs/modules/intelligence.md).
    None of them carry an interactive component yet — feedback.py's DM has no
    buttons on purpose (a working opt-out needs a handler plus stored state,
    which is a separate change). So there is nothing to attach here.

    The speaking-ratio DM goes through SlackClient.send_personal, which
    refuses any recipient but the subject and refuses a channel outright. See
    docs/architecture/privacy.md section 3.

    A module with nothing to register leaves this as a no-op; apps/bot calls it
    either way so no one has to edit the app to add a handler later.
    """
