"""retire the Workload and Tracker proposals that wait in the old shape (#959)

Data only; no table changes.

From #959 on, a Workload or Tracker proposal names its item's meeting in
``arguments`` and the waiting row is that meeting's. A row queued before that
has no ``meeting_id`` in its arguments, and its row meeting is the one whose
processing woke the run. Approved after the change it fails: the approval runs
under the row's meeting, module B's write now takes ``meeting_id``, so that
meeting is filled in for it -- and B refuses an item that is not that
meeting's (mkkim68, review of #998). Nothing wrong is written, but a manager
who clicks gets a failed card.

Those rows become ``superseded``, the status a later run gives the cards it
replaces. Both subagents judge the whole team and propose again on their next
run -- after a processed meeting, or on their timer -- in the new shape.
Decided rows are history and stay as they are, and so does every other
subagent's row.

The downgrade does nothing. Which rows this revision retired is not recorded,
so they cannot be told from rows a later run superseded; and bringing them
back would bring back cards whose approval fails. A retired proposal is not
lost: the next run makes it again.

Revision ID: a3d7c5e19f08
Revises: 8d4b1f6e2a53
Create Date: 2026-10-07 16:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a3d7c5e19f08"
down_revision: str | None = "8d4b1f6e2a53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE agent_pending_actions SET status = 'superseded'
        WHERE status = 'pending'
          AND subagent IN ('workload', 'tracker')
          AND arguments ->> 'meeting_id' IS NULL
        """
    )


def downgrade() -> None:
    """Nothing to undo -- see the module docstring."""
