"""SQLite in memory for the unit suite, the way module B's unit tests build tables.

Only the shared tables the agent's rows point at, plus the agent's own. Foreign
keys are not enforced here; ``tests/integration`` proves the cascades on
PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_agent.models import AgentApprover, AgentRun, AgentWorkItem
from autune_core import Base, Meeting, Team, TeamMember, User

TABLES = [
    Team.__table__,
    User.__table__,
    TeamMember.__table__,
    Meeting.__table__,
    AgentWorkItem.__table__,
    AgentRun.__table__,
    AgentApprover.__table__,
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture
def team(session: Session) -> dict[str, str]:
    team = Team(name="팀")
    member = User(email="member@example.com", display_name="팀원")
    outsider = User(email="outsider@example.com", display_name="외부")
    session.add_all([team, member, outsider])
    session.flush()
    session.add(TeamMember(team_id=team.id, user_id=member.id))
    meeting = Meeting(team_id=team.id, title="주간 회의")
    session.add(meeting)
    session.commit()
    return {"team": team.id, "member": member.id, "outsider": outsider.id, "meeting": meeting.id}
