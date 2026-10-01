"""A pending proposal goes with its meeting (spec section 4), on real PostgreSQL."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_agent.models import AgentPendingAction
from autune_core import Meeting, Team


def count(session: Session) -> int:
    return session.execute(sa.text("SELECT count(*) FROM agent_pending_actions")).scalar_one()


def test_deleting_a_meeting_deletes_its_pending_proposals(db_session: Session) -> None:
    team = Team(name="팀")
    db_session.add(team)
    db_session.flush()
    meeting = Meeting(team_id=team.id, title="회의")
    db_session.add(meeting)
    db_session.flush()
    db_session.add_all(
        [
            AgentPendingAction(
                team_id=team.id,
                meeting_id=meeting.id,
                subagent="research",
                tool="agent.share_research_document",
                kind="research_share",
                arguments={"document_id": "rdoc_1"},
                evidence=["rdoc_1"],
                scope="research",
            ),
            AgentPendingAction(
                team_id=team.id,
                subagent="workload",
                tool="extraction.reassign_action_item",
                kind="reassign",
                arguments={"action_item_id": "act_1", "assignee_id": "user_2"},
                evidence=["act_1"],
                scope="workload",
            ),
        ]
    )
    db_session.flush()

    db_session.execute(sa.text("DELETE FROM meetings WHERE id = :id"), {"id": meeting.id})

    assert count(db_session) == 1  # the proposal about no meeting stays


def test_report_is_an_approver_scope(db_session: Session) -> None:
    team = Team(name="팀")
    db_session.add(team)
    db_session.flush()
    db_session.execute(
        sa.text("INSERT INTO users (id, email, display_name) VALUES ('user_r', 'r@x.io', 'R')")
    )
    db_session.execute(
        sa.text(
            "INSERT INTO agent_approvers (team_id, user_id, scope) VALUES (:t, 'user_r', 'report')"
        ),
        {"t": team.id},
    )

    scopes = (
        db_session.execute(sa.text("SELECT scope FROM agent_approvers WHERE user_id = 'user_r'"))
        .scalars()
        .all()
    )
    assert scopes == ["report"]
