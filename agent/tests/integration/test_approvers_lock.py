"""Changing a team's approvers takes a lock on its rows (#621 review).

Without it two ``any`` approvers removing each other's ``any`` at once both
read the other as still holding it, both pass, and the team is left with
approver rows and no one who may change them.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from autune_agent.main.approvers import set_scopes
from autune_agent.models import AgentApprover
from autune_core import Team, TeamMember, User


@pytest.fixture
def two_managers(db_engine: sa.Engine) -> Iterator[dict[str, str]]:
    """A committed team whose two members both hold ``any``; deleted afterwards."""
    with Session(db_engine) as s:
        team = Team(name="잠금 팀")
        a = User(email="lock-a@example.com", display_name="가")
        b = User(email="lock-b@example.com", display_name="나")
        s.add_all([team, a, b])
        s.flush()
        s.add_all([TeamMember(team_id=team.id, user_id=u.id) for u in (a, b)])
        s.add_all([AgentApprover(team_id=team.id, user_id=u.id, scope="any") for u in (a, b)])
        s.commit()
        ids = {"team": team.id, "a": a.id, "b": b.id}
    yield ids
    with Session(db_engine) as s:
        s.execute(sa.delete(Team).where(Team.id == ids["team"]))
        s.execute(sa.delete(User).where(User.id.in_([ids["a"], ids["b"]])))
        s.commit()


def test_a_second_change_waits_for_the_first(
    db_engine: sa.Engine, two_managers: dict[str, str]
) -> None:
    t = two_managers
    with Session(db_engine) as first, Session(db_engine) as second:
        # A takes B's ``any`` away and has not committed yet.
        set_scopes(first, team_id=t["team"], user_id=t["b"], scopes=["report"], by=t["a"])

        second.execute(sa.text("SET lock_timeout = '300ms'"))
        with pytest.raises(OperationalError, match="lock"):
            # B, at the same moment, takes A's: it must wait, not read stale rows.
            set_scopes(second, team_id=t["team"], user_id=t["a"], scopes=["report"], by=t["b"])
        second.rollback()
        first.commit()

    with Session(db_engine) as s:
        held = set(
            s.execute(
                sa.select(AgentApprover.user_id, AgentApprover.scope).where(
                    AgentApprover.team_id == t["team"]
                )
            ).tuples()
        )
    assert held == {(t["a"], "any"), (t["b"], "report")}
