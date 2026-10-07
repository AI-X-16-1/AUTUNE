"""Two requests to leave one team at once, on PostgreSQL.

``service.leave_team`` locks every membership of the team before it counts
them. Two things rest on that lock and neither shows in a single session:

- the last two members leaving together are counted one after the other, so
  one of them is refused and the team is not left with nobody on it;
- the same person's second request, held on the first one's lock, finds its
  own row gone and is refused as for anybody not on the team -- not answered
  with a server error.

Same shape as ``test_claim_concurrency``: two real sessions, the second held on
the first's row lock, and ``pg_stat_activity`` as the witness.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_audio import service
from autune_audio.service import LastTeamMemberError, NotATeamMemberError
from autune_core.entities import Team, TeamMember, User


@pytest.fixture
def committed(db_engine: sa.Engine) -> Iterator[tuple[str, str, str]]:
    """A team of two, committed, so that two sessions can both see it."""
    with Session(db_engine) as session:
        team = Team(name="팀")
        host = User(email="leave-host@example.com", display_name="먼저 온 사람")
        mate = User(email="leave-mate@example.com", display_name="팀원")
        session.add_all([team, host, mate])
        session.flush()
        session.add(TeamMember(team_id=team.id, user_id=host.id))
        session.add(TeamMember(team_id=team.id, user_id=mate.id))
        session.commit()
        ids = (team.id, host.id, mate.id)
    try:
        yield ids
    finally:
        with Session(db_engine) as session:
            session.execute(sa.delete(Team).where(Team.id == ids[0]))
            session.execute(sa.delete(User).where(User.id.in_(ids[1:])))
            session.commit()


def waiting_on_a_lock(engine: sa.Engine, pid: int) -> bool:
    with engine.connect() as probe:
        return bool(
            probe.execute(
                sa.text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            ).scalar()
        )


def members(engine: sa.Engine, team_id: str) -> set[str]:
    with Session(engine) as session:
        return set(
            session.scalars(sa.select(TeamMember.user_id).where(TeamMember.team_id == team_id))
        )


def second_leave_after_first(
    engine: sa.Engine, team_id: str, first_user: str, second_user: str
) -> BaseException | str:
    """``first_user`` leaves and does not commit; ``second_user`` asks to leave
    and is seen waiting on that lock; then the first commits. What the second
    request came to: the exception it raised, or ``"left"``."""
    outcome: list[BaseException | str] = []

    first = Session(engine)
    leaver = first.get(User, first_user)
    assert leaver is not None
    service.leave_team(first, team_id=team_id, member=leaver)  # not committed

    second = Session(engine)
    second_pid = second.connection().exec_driver_sql("SELECT pg_backend_pid()").scalar_one()

    def other_request() -> None:
        try:
            other = second.get(User, second_user)
            assert other is not None
            service.leave_team(second, team_id=team_id, member=other)
            second.commit()
            outcome.append("left")
        except BaseException as caught:
            second.rollback()
            outcome.append(caught)

    thread = threading.Thread(target=other_request)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not waiting_on_a_lock(engine, second_pid):
            assert thread.is_alive(), "the second request finished without waiting on the first"
            assert time.monotonic() < deadline, "the second request never reached the first's lock"
            time.sleep(0.05)
        first.commit()
        thread.join(timeout=10)
    finally:
        first.close()
        second.close()

    assert not thread.is_alive()
    (result,) = outcome
    return result


def test_the_last_two_members_cannot_both_leave(
    db_engine: sa.Engine, committed: tuple[str, str, str]
) -> None:
    team_id, host_id, mate_id = committed

    result = second_leave_after_first(db_engine, team_id, mate_id, host_id)

    assert isinstance(result, LastTeamMemberError), result
    assert members(db_engine, team_id) == {host_id}


def test_the_same_persons_second_request_is_refused_and_not_an_error(
    db_engine: sa.Engine, committed: tuple[str, str, str]
) -> None:
    """A double click. The second request passed the membership check before the
    first committed, so after the lock its own row is no longer there."""
    team_id, host_id, mate_id = committed

    result = second_leave_after_first(db_engine, team_id, mate_id, mate_id)

    assert isinstance(result, NotATeamMemberError), result
    assert members(db_engine, team_id) == {host_id}
