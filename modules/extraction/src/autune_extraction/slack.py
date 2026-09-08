"""Slack handlers for module B.

Collected automatically by apps/bot. Handlers acknowledge and delegate to
``service`` — no business logic here, the same rule as router.py and tasks.py.

Surface: action-item card thread, ambiguous-agreement DM.
See docs/modules/extraction.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from autune_core import get_logger

if TYPE_CHECKING:
    from slack_bolt import App

log = get_logger(__name__)


def register(app: App) -> None:
    """Attach this module's Slack handlers.

    A module with nothing to register leaves this as a no-op; apps/bot calls it
    either way so no one has to edit the app to add a handler later.
    """
    # TODO(강민구): register commands, actions and views for this module.
