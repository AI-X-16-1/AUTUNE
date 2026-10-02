"""Workload wakes on its own, on module B's real tools.

Mentoring (2026-10-01): the agent should act without being asked. Workload
wakes after a meeting is processed (``INTELLIGENCE_COMPLETED``, when new items
land on people) and every six hours per team (``Periodic``, #634, because a
load also changes between meetings). Under test: the event runs it for the
meeting's team only; the timer runs it for each team; its reassignments wait
for the manager as L2 rows and nothing is written to B; and a later run,
whichever way it woke, replaces the proposals still waiting (#631).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_agent.main import Periodic, on_event, on_tick
from autune_agent.models import AgentPendingAction, AgentRun
from autune_agent.subagents.workload import SUBAGENT
from autune_agent.subagents.workload.graph import REASSIGN
from autune_contracts import INTELLIGENCE_COMPLETED
from autune_core import Base, Meeting, Team, TeamMember, User, Utterance
from autune_extraction import service
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import ExtActionItem

TODAY = date.today()


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Team, User, TeamMember, Meeting, Utterance)}
    tables = [
        t
        for name, t in Base.metadata.tables.items()
        if name in shared or name.startswith(("ext_", "agent_"))
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture
def team(session: Session) -> dict[str, str]:
    """박지영 holds 8 open (2 late), 김민경 3, 이승환 none; two meetings. Another
    team's one member holds 4 of her own."""
    people = {"u_park": "박지영", "u_kim": "김민경", "u_lee": "이승환", "u_other": "타팀원"}
    session.add_all([Team(id="team_a", name="A팀"), Team(id="team_b", name="B팀")])
    for uid, name in people.items():
        session.add(User(id=uid, email=f"{uid}@example.com", display_name=name))
    session.flush()
    for uid in ("u_park", "u_kim", "u_lee"):
        session.add(TeamMember(team_id="team_a", user_id=uid))
    session.add(TeamMember(team_id="team_b", user_id="u_other"))
    for mid, tid in (("mtg_1", "team_a"), ("mtg_2", "team_a"), ("mtg_b", "team_b")):
        session.add(Meeting(id=mid, team_id=tid, title="주간 회의"))
    session.flush()

    def item(item_id: str, owner: str, meeting: str, *, late: bool = False) -> None:
        due = TODAY - timedelta(days=2) if late else TODAY + timedelta(days=5)
        session.add(
            ExtActionItem(
                id=item_id,
                meeting_id=meeting,
                description=f"{people[owner]}의 할 일",
                assignee_id=owner,
                due_date=due,
                status="todo",
                confidence=0.9,
                origin="user",
            )
        )

    for n in range(8):
        item(f"act_park{n}", "u_park", "mtg_1", late=n < 2)
    for n in range(3):
        item(f"act_kim{n}", "u_kim", "mtg_1")
    for n in range(4):
        item(f"act_other{n}", "u_other", "mtg_b")
    session.commit()
    return {"team": "team_a", "other": "team_b"}


def _wake(session: Session, meeting_id: str) -> list[AgentRun]:
    return on_event(
        INTELLIGENCE_COMPLETED, meeting_id, session=session, subagents={"workload": SUBAGENT}
    )


def _pending(session: Session) -> list[AgentPendingAction]:
    return list(
        session.scalars(select(AgentPendingAction).where(AgentPendingAction.status == "pending"))
    )


def test_workload_asks_for_the_processed_meeting_and_a_timer() -> None:
    assert INTELLIGENCE_COMPLETED in SUBAGENT.triggers
    assert SUBAGENT.period == Periodic(hours=6)
    assert SUBAGENT.proposals_per == "team", "its proposals judge the whole team (#631)"


def test_a_processed_meeting_proposes_moves_for_the_manager_and_runs_none(
    session: Session, team: dict[str, str]
) -> None:
    before = dict(session.execute(select(ExtActionItem.id, ExtActionItem.assignee_id)).all())

    (run,) = _wake(session, "mtg_1")

    assert run.route == "workload"
    assert run.team_id == team["team"]
    pending = _pending(session)
    assert [(p.tool, p.scope) for p in pending] == [(REASSIGN, "workload")] * 2
    assert sorted(e for p in pending for e in p.evidence) == ["act_park0", "act_park1"]
    assert run.actions == []
    after = dict(session.execute(select(ExtActionItem.id, ExtActionItem.assignee_id)).all())
    assert after == before


def test_another_teams_meeting_reads_only_that_team(session: Session, team: dict[str, str]) -> None:
    (run,) = _wake(session, "mtg_b")

    assert run.team_id == team["other"]
    assert _pending(session) == [], "a one-person team is never loaded"


def test_a_later_meeting_replaces_the_moves_still_waiting(
    session: Session, team: dict[str, str]
) -> None:
    (first,) = _wake(session, "mtg_1")
    (second,) = _wake(session, "mtg_2")

    pending = _pending(session)
    assert {p.run_id for p in pending} == {second.id}
    assert len(pending) == 2, "one set of moves waits, not two"


def test_the_timer_runs_it_for_each_team_and_replaces_what_a_meeting_left(
    session: Session, team: dict[str, str]
) -> None:
    """A periodic run is about the team, not a meeting (#634). Its proposals
    still replace the ones a meeting's run left waiting."""
    _wake(session, "mtg_1")

    runs = on_tick(session=session, subagents={"workload": SUBAGENT})

    by_team = {run.team_id: run for run in runs}
    assert set(by_team) == {team["team"], team["other"]}
    assert {run.trigger["kind"] for run in runs} == {"periodic"}
    assert {run.meeting_id for run in runs} == {None}
    pending = _pending(session)
    assert {p.run_id for p in pending} == {by_team[team["team"]].id}
    assert [(p.tool, p.scope) for p in pending] == [(REASSIGN, "workload")] * 2
