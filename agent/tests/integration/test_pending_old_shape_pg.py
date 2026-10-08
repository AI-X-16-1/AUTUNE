"""The revision that retires old-shape Workload and Tracker proposals, run by
Alembic on PostgreSQL (#959; mkkim68, review of #998).

A proposal queued before #959 names no meeting in its arguments. Approved
after it, the row's meeting -- the one that woke the run -- is filled in for
module B's write, and B refuses an item of another meeting
(``test_tracker_approval`` holds that failure). Revision ``a3d7c5e19f08``
supersedes those rows so that nobody is left a card that fails; the next run
proposes them again in the new shape.

The rows are committed, because Alembic runs in its own process, and removed
at the end with their team.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_agent.models import AgentPendingAction
from autune_core import Meeting, Team

ALEMBIC = ["uv", "run", "alembic", "-c", "infra/alembic.ini"]
BEFORE = "8d4b1f6e2a53"
"""The agent branch's head before the revision under test."""

REASSIGN = "extraction.reassign_action_item"
MOVE = "extraction.set_action_item_due_date"


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "infra" / "alembic.ini").is_file():
            return parent
    raise RuntimeError("could not locate repo root (infra/alembic.ini not found)")


def row(
    team_id: str,
    subagent: str,
    tool: str,
    arguments: dict[str, Any],
    *,
    meeting_id: str | None = None,
    status: str = "pending",
) -> AgentPendingAction:
    return AgentPendingAction(
        team_id=team_id,
        meeting_id=meeting_id,
        subagent=subagent,
        tool=tool,
        kind="k",
        arguments=arguments,
        evidence=[],
        scope="any",
        status=status,
    )


@pytest.fixture
def waiting(db_engine: sa.Engine) -> Iterator[dict[str, str]]:
    """The agent branch one revision back, with rows of both shapes waiting."""
    root = repo_root()
    subprocess.run([*ALEMBIC, "downgrade", f"agent@{BEFORE}"], check=True, cwd=root)
    with Session(db_engine) as session:
        team = Team(name="팀")
        session.add(team)
        session.flush()
        woke = Meeting(team_id=team.id, title="방금 처리된 회의")
        items = Meeting(team_id=team.id, title="항목의 회의")
        session.add_all([woke, items])
        session.flush()
        old = {"action_item_id": "act_1", "assignee_id": "user_2"}
        rows = {
            # Queued before #959: no meeting named.
            "old_workload_woken": row(team.id, "workload", REASSIGN, old, meeting_id=woke.id),
            "old_tracker_timer": row(
                team.id, "tracker", MOVE, {"action_item_id": "act_2", "due_date": "2026-10-14"}
            ),
            "old_null": row(team.id, "workload", REASSIGN, {**old, "meeting_id": None}),
            # Queued after it: the item's meeting named, and the row is that meeting's.
            "new_workload": row(
                team.id, "workload", REASSIGN, {**old, "meeting_id": items.id}, meeting_id=items.id
            ),
            "new_tracker": row(
                team.id,
                "tracker",
                MOVE,
                {"action_item_id": "act_2", "due_date": "2026-10-14", "meeting_id": items.id},
                meeting_id=items.id,
            ),
            # Not theirs, and not waiting.
            "followup": row(
                team.id, "followup", "extraction.add_followup_item", {}, meeting_id=woke.id
            ),
            "research": row(
                team.id, "research", "agent.share_research_document", {"document_id": "rdoc_1"}
            ),
            "old_approved": row(team.id, "workload", REASSIGN, old, status="approved"),
            "old_rejected": row(team.id, "tracker", MOVE, old, status="rejected"),
            "old_failed": row(team.id, "workload", REASSIGN, old, status="failed"),
        }
        session.add_all(rows.values())
        session.commit()
        ids = {name: r.id for name, r in rows.items()}
        ids["team"] = team.id
    try:
        yield ids
    finally:
        subprocess.run([*ALEMBIC, "upgrade", "heads"], check=True, cwd=root)
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id == ids["team"]))
            session.commit()


def statuses(engine: sa.Engine, ids: dict[str, str]) -> dict[str, str]:
    by_id = {v: k for k, v in ids.items() if k != "team"}
    with Session(engine) as session:
        found = session.execute(
            sa.select(AgentPendingAction.id, AgentPendingAction.status).where(
                AgentPendingAction.team_id == ids["team"]
            )
        ).all()
    return {by_id[row_id]: status for row_id, status in found}


def test_old_shape_cards_of_the_two_are_retired_and_everything_else_is_left(
    db_engine: sa.Engine, waiting: dict[str, str]
) -> None:
    before = statuses(db_engine, waiting)
    assert set(before.values()) == {"pending", "approved", "rejected", "failed"}

    subprocess.run([*ALEMBIC, "upgrade", "heads"], check=True, cwd=repo_root())

    assert statuses(db_engine, waiting) == {
        "old_workload_woken": "superseded",
        "old_tracker_timer": "superseded",
        "old_null": "superseded",
        "new_workload": "pending",
        "new_tracker": "pending",
        "followup": "pending",
        "research": "pending",
        "old_approved": "approved",
        "old_rejected": "rejected",
        "old_failed": "failed",
    }


def test_the_downgrade_brings_nothing_back(db_engine: sa.Engine, waiting: dict[str, str]) -> None:
    """A retired card is not told from one a later run replaced, and coming
    back it would fail again. The schema is the same either side."""
    root = repo_root()
    subprocess.run([*ALEMBIC, "upgrade", "heads"], check=True, cwd=root)
    after = statuses(db_engine, waiting)

    subprocess.run([*ALEMBIC, "downgrade", f"agent@{BEFORE}"], check=True, cwd=root)

    assert statuses(db_engine, waiting) == after
    assert after["old_workload_woken"] == "superseded"
