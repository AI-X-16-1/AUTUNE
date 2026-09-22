"""``python -m autune_extraction.notion_backfill`` -- send Notion pages for
already-confirmed items and decisions the automatic sync never saw (#317).

``tasks.sync_after_confirmation`` and ``sync_decision_after_confirmation`` only
run from the confirm endpoint's ``BackgroundTasks``, at the moment a person
confirms something. An item or decision that was already confirmed before its
team connected Notion -- or before #312 shipped the sync at all -- passed that
moment with nothing listening, and nothing about its state says so: the board
shows it as confirmed either way.

This walks every already-confirmed row through the same idempotent
claim-then-call path the live sync uses
(``service.sync_action_item_to_notion`` / ``sync_decision_to_notion``), so it
is **safe to run more than once**: a row that already has its page is claimed
by nobody, `find`s its existing ``ext_external_refs`` / ``ext_decision_refs``
row and is skipped, exactly as a redelivered confirmation already is.

    uv run python -m autune_extraction.notion_backfill
    uv run python -m autune_extraction.notion_backfill --team team_abc123
    uv run python -m autune_extraction.notion_backfill --dry-run

Prints counts only -- no description, statement, team id or team name.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from sqlalchemy import select

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, get_logger, load_integration, session_scope
from autune_integrations import IntegrationError, NotionClient

from . import service
from .models import ExtActionItem, ExtDecisionReview

log = get_logger(__name__)


@dataclass
class Stats:
    """Counts only -- see module docstring."""

    sent: int = 0
    already_synced: int = 0
    not_connected: int = 0
    failed: int = 0


def _confirmed_action_items(team_id: str | None) -> list[tuple[str, str]]:
    """``(action_item_id, meeting_id)`` for every item past ``needs_confirmation``."""
    with session_scope() as session:
        stmt = select(ExtActionItem.id, ExtActionItem.meeting_id).where(
            ExtActionItem.status != ActionStatus.NEEDS_CONFIRMATION.value
        )
        if team_id is not None:
            stmt = stmt.join(Meeting, Meeting.id == ExtActionItem.meeting_id).where(
                Meeting.team_id == team_id
            )
        return list(session.execute(stmt).tuples().all())


def _confirmed_decisions(team_id: str | None) -> list[tuple[str, str]]:
    """``(decision_id, meeting_id)`` for every review a person confirmed."""
    with session_scope() as session:
        stmt = select(ExtDecisionReview.decision_id, ExtDecisionReview.meeting_id).where(
            ExtDecisionReview.status == "confirmed"
        )
        if team_id is not None:
            stmt = stmt.join(Meeting, Meeting.id == ExtDecisionReview.meeting_id).where(
                Meeting.team_id == team_id
            )
        return list(session.execute(stmt).tuples().all())


class _ClientCache:
    """One ``NotionClient`` per team, not one per row.

    A backfill can walk hundreds of rows across a handful of teams; opening a
    fresh HTTP client for every row throws away the connection the previous
    row for the same team just made.
    """

    def __init__(self) -> None:
        self._clients: dict[str, NotionClient] = {}

    def get(self, team_id: str, secret: str) -> NotionClient:
        client = self._clients.get(team_id)
        if client is None:
            client = NotionClient(secret)
            self._clients[team_id] = client
        return client


def _sync_one_action_item(
    action_item_id: str, meeting_id: str, stats: Stats, clients: _ClientCache
) -> None:
    """Its own claim-then-call transaction, the same unit ``tasks.sync_action_item``
    commits. Left to raise ``IntegrationError`` rather than catching it here: caught
    inside this ``with`` block, ``session_scope`` would see no exception and commit
    the claim a failed call made, the way ``tasks.sync_after_confirmation`` avoids
    it by catching one call *outside* ``session_scope``, not inside it."""
    with session_scope() as session:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None:
            return
        config = load_integration(session, meeting.team_id, "notion")
        database_id = config.config.get("action_db_id") if config is not None else None
        if config is None or not config.secret or not database_id:
            stats.not_connected += 1
            return
        ref = service.sync_action_item_to_notion(
            session,
            clients.get(meeting.team_id, config.secret),
            action_item_id=action_item_id,
            database_id=database_id,
            property_names=config.config.get("action_properties"),
        )
        if ref is None:
            stats.already_synced += 1
        else:
            stats.sent += 1


def backfill_action_items(rows: list[tuple[str, str]], stats: Stats) -> None:
    clients = _ClientCache()
    for action_item_id, meeting_id in rows:
        try:
            _sync_one_action_item(action_item_id, meeting_id, stats, clients)
        except IntegrationError:
            log.warning("extraction_notion_backfill_failed", action_item_id=action_item_id)
            stats.failed += 1


def _sync_one_decision(
    decision_id: str, meeting_id: str, stats: Stats, clients: _ClientCache
) -> None:
    """``_sync_one_action_item``'s rules, for a decision."""
    with session_scope() as session:
        meeting = session.get(Meeting, meeting_id)
        if meeting is None:
            return
        config = load_integration(session, meeting.team_id, "notion")
        database_id = config.config.get("decision_db_id") if config is not None else None
        if config is None or not config.secret or not database_id:
            stats.not_connected += 1
            return
        ref = service.sync_decision_to_notion(
            session,
            clients.get(meeting.team_id, config.secret),
            decision_id=decision_id,
            database_id=database_id,
            property_names=config.config.get("decision_properties"),
        )
        if ref is None:
            stats.already_synced += 1
        else:
            stats.sent += 1


def backfill_decisions(rows: list[tuple[str, str]], stats: Stats) -> None:
    clients = _ClientCache()
    for decision_id, meeting_id in rows:
        try:
            _sync_one_decision(decision_id, meeting_id, stats, clients)
        except IntegrationError:
            log.warning("extraction_notion_backfill_failed", decision_id=decision_id)
            stats.failed += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autune_extraction.notion_backfill", description=__doc__)
    parser.add_argument("--team", help="only this team's meetings; default every team")
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would be sent, send nothing"
    )
    args = parser.parse_args(argv)

    items = _confirmed_action_items(args.team)
    decisions = _confirmed_decisions(args.team)
    print(f"action items already confirmed: {len(items)}")
    print(f"decisions already confirmed:    {len(decisions)}")
    if args.dry_run:
        return 0

    item_stats, decision_stats = Stats(), Stats()
    backfill_action_items(items, item_stats)
    backfill_decisions(decisions, decision_stats)

    for label, stats in (("action items", item_stats), ("decisions", decision_stats)):
        print(
            f"{label:<13} sent {stats.sent:<5} already-synced {stats.already_synced:<5} "
            f"team not connected {stats.not_connected:<5} failed {stats.failed}"
        )

    return 1 if (item_stats.failed or decision_stats.failed) else 0


if __name__ == "__main__":
    sys.exit(main())
