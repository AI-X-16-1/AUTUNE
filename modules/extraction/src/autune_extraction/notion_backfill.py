"""``python -m autune_extraction.notion_backfill`` -- send Notion pages for
already-confirmed items and decisions the automatic sync never saw (#317).

``tasks.sync_after_confirmation`` and ``sync_decision_after_confirmation`` only
run from the confirm endpoint's ``BackgroundTasks``, at the moment a person
confirms something. An item or decision that was already confirmed before its
team connected Notion -- or before #312 shipped the sync at all -- passed that
moment with nothing listening, and nothing about its state says so: the board
shows it as confirmed either way.

This walks every already-confirmed row through the same claim-then-call path
the live sync uses (``service.sync_action_item_to_notion`` /
``sync_decision_to_notion``), so it is **safe to run more than once**: a row
with no page yet gets one created; a row that already has one gets it
updated with whatever the description, assignee, due date or statement read
as now, the same way a later edit on the board does. A page someone deleted in
Notion is made again and counted as ``replaced``; one someone archived is left
archived and counted as ``archived`` -- a backfill does not undo a person's
tidying (#404).

It also retires decision pages that should no longer be there (#669): a page
still recorded for a decision that is not confirmed any more, or is gone. The
live sync retires such a page once, when the verdict changes or the decision
is deleted, and only logs a failure; a deleted decision has no later event to
try again on. The second try is here, when a team connects Notion
(``tasks.backfill_notion``) or someone runs this command -- and, without
anyone doing anything, in ``tasks.retire_decision_pages``, which runs the
same list on a timer (#683).

    uv run python -m autune_extraction.notion_backfill
    uv run python -m autune_extraction.notion_backfill --team team_abc123
    uv run python -m autune_extraction.notion_backfill --dry-run

Prints counts only -- no description, statement, team id or team name.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from sqlalchemy import or_, select

from autune_contracts.enums import ActionStatus
from autune_core import Meeting, PrivacyViolationError, get_logger, load_integration, session_scope
from autune_integrations import IntegrationError, NotionClient

from . import notion_setup, service
from .models import ExtActionItem, ExtDecisionRef, ExtDecisionReview, ExtExternalRef

log = get_logger(__name__)


@dataclass
class Stats:
    """Counts only -- see module docstring."""

    sent: int = 0
    updated: int = 0
    replaced: int = 0
    """A page deleted in Notion, made again."""
    archived: int = 0
    """A page archived in Notion, left as it is."""
    retired: int = 0
    """A page of a decision no longer confirmed, taken out of Notion (#669)."""
    not_connected: int = 0
    failed: int = 0

    def count(self, outcomes: list[service.PageOutcome]) -> None:
        """What became of a page the row already had. Nothing reported means
        no call was made for it."""
        for outcome in outcomes:
            setattr(self, outcome, getattr(self, outcome) + 1)


def _confirmed_action_items(team_id: str | None) -> list[tuple[str, str]]:
    """``(action_item_id, meeting_id)`` for every item past ``needs_confirmation``,
    and every one moved back to it that has a page -- its page shows 확인 필요
    (#622), and a page written before #622 still shows a status code."""
    with session_scope() as session:
        has_page = (
            select(ExtExternalRef.action_item_id)
            .where(
                ExtExternalRef.action_item_id == ExtActionItem.id,
                ExtExternalRef.system == "notion",
            )
            .exists()
        )
        stmt = select(ExtActionItem.id, ExtActionItem.meeting_id).where(
            or_(ExtActionItem.status != ActionStatus.NEEDS_CONFIRMATION.value, has_page)
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


def _decision_pages_to_retire(team_id: str | None) -> list[tuple[str, str]]:
    """``(decision_id, meeting_id)`` for every decision page B still records
    whose decision is not confirmed -- put back, rejected, or deleted (#669).

    Read from the ref rows, since a deleted decision has no other row left.
    ``sync_decision_to_notion`` retires each; a row whose page is already
    retired has no page id and is not listed."""
    with session_scope() as session:
        confirmed = (
            select(ExtDecisionReview.decision_id)
            .where(
                ExtDecisionReview.decision_id == ExtDecisionRef.decision_id,
                ExtDecisionReview.status == "confirmed",
            )
            .exists()
        )
        stmt = select(ExtDecisionRef.decision_id, ExtDecisionRef.meeting_id).where(
            ExtDecisionRef.system == "notion",
            ExtDecisionRef.external_id.is_not(None),
            ~confirmed,
        )
        if team_id is not None:
            stmt = stmt.join(Meeting, Meeting.id == ExtDecisionRef.meeting_id).where(
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
        database_id = notion_setup.database_id(session, meeting.team_id, config, "action_db_id")
        if config is None or not config.secret or not database_id:
            stats.not_connected += 1
            return
        already_had_a_page = session.get(ExtExternalRef, (action_item_id, "notion")) is not None
        outcomes: list[service.PageOutcome] = []
        ref = service.sync_action_item_to_notion(
            session,
            clients.get(meeting.team_id, config.secret),
            action_item_id=action_item_id,
            database_id=database_id,
            property_names=config.config.get("action_properties"),
            on_page=outcomes.append,
        )
        if ref is not None:
            if already_had_a_page:
                stats.count(outcomes)
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
        except PrivacyViolationError:
            # A sibling of IntegrationError, not a subclass -- check_outbound
            # raises this one when the description or assignee label still
            # carries unmasked PII. Caught here too, or one blocked row would
            # crash the whole batch instead of costing only itself (#333).
            log.warning(
                "extraction_notion_backfill_blocked_by_privacy_guard", action_item_id=action_item_id
            )
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
        database_id = notion_setup.database_id(session, meeting.team_id, config, "decision_db_id")
        if config is None or not config.secret or not database_id:
            stats.not_connected += 1
            return
        before = session.get(ExtDecisionRef, (decision_id, "notion"))
        already_had_a_page = before is not None
        had_page_id = before is not None and before.external_id is not None
        outcomes: list[service.PageOutcome] = []
        ref = service.sync_decision_to_notion(
            session,
            clients.get(meeting.team_id, config.secret),
            decision_id=decision_id,
            database_id=database_id,
            property_names=config.config.get("decision_properties"),
            on_page=outcomes.append,
        )
        if ref is not None:
            if had_page_id and ref.external_id is None:
                stats.retired += 1
            elif already_had_a_page:
                stats.count(outcomes)
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
        except PrivacyViolationError:
            log.warning(
                "extraction_notion_backfill_blocked_by_privacy_guard", decision_id=decision_id
            )
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
    stale = _decision_pages_to_retire(args.team)
    print(f"action items already confirmed: {len(items)}")
    print(f"decisions already confirmed:    {len(decisions)}")
    print(f"decision pages to retire:       {len(stale)}")
    if args.dry_run:
        return 0

    item_stats, decision_stats = Stats(), Stats()
    backfill_action_items(items, item_stats)
    backfill_decisions(decisions, decision_stats)
    backfill_decisions(stale, decision_stats)

    for label, stats in (("action items", item_stats), ("decisions", decision_stats)):
        print(
            f"{label:<13} sent {stats.sent:<5} updated {stats.updated:<5} "
            f"replaced {stats.replaced:<5} archived, left alone {stats.archived:<5} "
            f"team not connected {stats.not_connected:<5} failed {stats.failed}"
        )

    print(f"decision pages retired: {decision_stats.retired}")

    return 1 if (item_stats.failed or decision_stats.failed) else 0


if __name__ == "__main__":
    sys.exit(main())
