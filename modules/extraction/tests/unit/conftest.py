"""Shared unit-test setup for module B."""

from __future__ import annotations

import pytest

from autune_extraction import tasks

SYNC_ACTION_ITEM_CALENDAR = tasks.sync_action_item_calendar
"""The real task, for ``test_calendar_sync`` to put back."""

REMOVE_CALENDAR_EVENT = tasks.remove_calendar_event


@pytest.fixture(autouse=True)
def _no_calendar_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirming an item through the router runs ``sync_after_confirmation``,
    whose calendar half opens its own ``session_scope`` -- a real database these
    tests do not have; deleting one does the same for its calendar event. Tests
    that exercise them put the real ones back themselves."""
    monkeypatch.setattr(tasks, "sync_action_item_calendar", lambda _action_item_id: None)
    monkeypatch.setattr(tasks, "remove_calendar_event", lambda _action_item_id: None)
