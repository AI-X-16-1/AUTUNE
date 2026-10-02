"""Shared unit-test setup for module B."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from autune_core import TeamMember, User
from autune_core.auth import current_user
from autune_extraction import tasks

READER = "user_reader"
"""The signed-in caller of router tests: a member of ``team_1``, where every
fixture meeting is held, and of no other team."""

SYNC_ACTION_ITEM_CALENDAR = tasks.sync_action_item_calendar
"""The real task, for ``test_calendar_sync`` to put back."""

REMOVE_CALENDAR_EVENT = tasks.remove_calendar_event

SYNC_ACTION_ITEM_JIRA = tasks.sync_action_item_jira

CLOSE_JIRA_ISSUE = tasks.close_jira_issue

TRASH_NOTION_PAGE = tasks.trash_notion_page

SYNC_FAILED = tasks._sync_failed

SYNC_WENT = tasks._sync_went


@pytest.fixture(autouse=True)
def _no_calendar_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirming an item through the router runs ``sync_after_confirmation``,
    whose calendar half opens its own ``session_scope`` -- a real database these
    tests do not have; deleting one does the same for its calendar event. Tests
    that exercise them put the real ones back themselves."""
    monkeypatch.setattr(tasks, "sync_action_item_calendar", lambda _action_item_id: None)
    monkeypatch.setattr(tasks, "remove_calendar_event", lambda _action_item_id: None)
    monkeypatch.setattr(tasks, "sync_action_item_jira", lambda _action_item_id: None)
    monkeypatch.setattr(tasks, "close_jira_issue", lambda _action_item_id: None)
    monkeypatch.setattr(tasks, "trash_notion_page", lambda _action_item_id: None)
    # The bookkeeping after each copy (#680) opens its own ``session_scope``
    # too. ``test_sync_failures`` puts the real ones back.
    monkeypatch.setattr(tasks, "_sync_failed", lambda _id, _system, _exc: None)
    monkeypatch.setattr(tasks, "_sync_went", lambda _id, _system: None)


def sign_in(app: FastAPI, session: Session, *, team_id: str = "team_1") -> None:
    """Every route but /health takes ``CurrentUser`` (#189). Make ``READER`` the
    caller and put them on ``team_id``. ``team_members`` must be in the test's
    tables; the ``User`` is never written, so ``users`` need not be."""
    session.add(TeamMember(team_id=team_id, user_id=READER))
    session.flush()
    reader = User(id=READER, email=f"{READER}@example.com", display_name="읽는 사람")
    app.dependency_overrides[current_user] = lambda: reader
