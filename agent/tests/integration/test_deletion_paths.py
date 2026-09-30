"""Every ``agent_`` row that copies a meeting leaves with it, on a real PostgreSQL.

privacy.md section 7 rejects a table with no path to deletion by ``meeting_id``
or ``user_id``, and agent-layer.md section 5 names both tables that copy meeting
content. The path is ``ON DELETE CASCADE``, which only the database enforces --
so these delete with plain SQL, the way a retention sweep or an account deletion
would, never through the ORM.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_agent.models import AgentApprover, AgentRun, AgentWorkItem
from autune_core import Meeting, Team, User


def count(session: Session, table: str) -> int:
    return session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608


def _seed(session: Session) -> dict[str, str]:
    team = Team(name="팀")
    user = User(email="lead@example.com", display_name="팀장")
    session.add_all([team, user])
    session.flush()
    meeting = Meeting(team_id=team.id, title="주간 회의")
    session.add(meeting)
    session.flush()
    session.add_all(
        [
            AgentWorkItem(
                team_id=team.id,
                kind="action",
                title="API 문서 올리기",
                origin_meeting=meeting.id,
                origin_utterances=["utt_aa11"],
                owner_id=user.id,
            ),
            AgentRun(
                team_id=team.id,
                meeting_id=meeting.id,
                trigger={"kind": "event"},
                outcome="answered",
                answer="마감 1건.",
            ),
            AgentRun(team_id=team.id, trigger={"kind": "chat"}, outcome="unrouted"),
            AgentApprover(team_id=team.id, user_id=user.id, scope="followup"),
        ]
    )
    session.flush()
    return {"team": team.id, "user": user.id, "meeting": meeting.id}


def test_deleting_a_meeting_deletes_what_the_agent_copied_from_it(db_session: Session) -> None:
    ids = _seed(db_session)

    db_session.execute(sa.text("DELETE FROM meetings WHERE id = :id"), {"id": ids["meeting"]})

    assert count(db_session, "agent_work_items") == 0
    # The chat run about no meeting stays: it holds tool names and ids, no text.
    assert db_session.execute(sa.text("SELECT outcome FROM agent_runs")).scalars().all() == [
        "unrouted"
    ]


def test_deleting_a_user_deletes_their_approver_rows(db_session: Session) -> None:
    ids = _seed(db_session)

    db_session.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": ids["user"]})

    assert count(db_session, "agent_approvers") == 0
    owner = db_session.execute(sa.text("SELECT owner_id FROM agent_work_items")).scalar_one()
    assert owner is None


def test_a_kind_outside_the_list_is_refused(db_session: Session) -> None:
    ids = _seed(db_session)

    try:
        with db_session.begin_nested():
            db_session.add(AgentWorkItem(team_id=ids["team"], kind="gossip", title="x"))
            db_session.flush()
    except sa.exc.IntegrityError:
        return
    raise AssertionError("ck_agent_work_items_kind let 'gossip' through")
