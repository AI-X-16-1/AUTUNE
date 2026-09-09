"""Slack handlers for module B.

Collected automatically by apps/bot. Handlers acknowledge and delegate to
``service`` — no business logic here, the same rule as router.py and tasks.py.

Surface: action-item card thread, ambiguous-agreement DM.
See docs/modules/extraction.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from autune_core import get_logger

from .confirmations import (
    CONFIRM_COMMITMENT,
    CONFIRM_DECISION,
    DENY,
    ConfirmationError,
    parse_confirmation_action,
)
from .service import apply_confirmation_response

if TYPE_CHECKING:
    from slack_bolt import App

log = get_logger(__name__)

CONFIRMATION_ACTIONS = (CONFIRM_COMMITMENT, CONFIRM_DECISION, DENY)


def register(app: App) -> None:
    """Attach this module's Slack handlers.

    A module with nothing to register leaves this as a no-op; apps/bot calls it
    either way so no one has to edit the app to add a handler later.
    """
    for action_id in CONFIRMATION_ACTIONS:
        app.action(action_id)(_on_confirmation)


def _on_confirmation(ack: Any, body: dict) -> None:
    """One button on the ambiguous-agreement DM.

    Acknowledges first. Slack retries anything it has not heard back from within
    three seconds, and a retry of this arrives as a second click on the same
    utterance — which is why ``apply_confirmation_response`` has to be idempotent
    rather than merely fast.
    """
    ack()
    try:
        response = parse_confirmation_action(body)
    except ConfirmationError as exc:
        # The message is written to carry no meeting content; see confirmations.
        log.warning("extraction_confirmation_unparsed", reason=str(exc))
        return

    apply_confirmation_response(response)
