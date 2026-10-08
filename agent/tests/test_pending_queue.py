"""L2 proposals are queued; free text is refused; the newer run supersedes."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_agent.main import Subagent, SubagentState, Toolbox
from autune_agent.main.actions import KEPT_FOR_APPROVAL, Action
from autune_agent.main.pending import (
    ARGUMENT_REFUSED,
    MEETING_NOT_FOUND,
    arguments_ok,
    queue_l2,
    scope_for,
)
from autune_agent.main.store import run_and_record
from autune_agent.models import AgentPendingAction, AgentRun
from autune_agent.results import ProposedAction, SubagentResult, ToolResult
from autune_agent.testing import FakeRouter
from autune_core import Meeting, Team


def _run(
    session: Session,
    team: dict[str, str],
    route: str,
    meeting: str | None,
    trigger: dict[str, Any] | None = None,
) -> AgentRun:
    row = AgentRun(
        team_id=team["team"],
        meeting_id=meeting,
        trigger=trigger or {"kind": "event", "event": "autune.intelligence.completed"},
        outcome="answered",
        route=route,
    )
    session.add(row)
    session.flush()
    return row


def _l2(tool: str, *, kind: str = "k", **arguments: Any) -> ProposedAction:
    return ProposedAction(
        kind=kind,
        title="t",
        tool=tool,
        arguments=arguments,
        level="L2",
        rationale="r",
        evidence=[v for v in arguments.values() if isinstance(v, str) and "_" in v][:1],
    )


@pytest.mark.parametrize(
    ("arguments", "ok"),
    [
        ({"document_id": "rdoc_1"}, True),
        ({"due_date": "2026-10-09", "action_item_id": "act_1"}, True),
        ({"status": "in_progress", "notify": True}, True),
        ({"description": "김 팀장이 금요일까지 배포"}, False),
        ({"status": "x" * 33}, False),
        ({"ids": ["act_1"]}, False),
        ({"김 팀장이 금요일까지 배포": True}, False),
        ({"Status": "in_progress"}, False),
        ({"k" * 33: True}, False),
        ({"due_date": "٢٠٢٦-١٠-٠٩"}, False),
    ],
)
def test_only_ids_and_short_values_pass(arguments: dict[str, Any], ok: bool) -> None:
    assert arguments_ok(arguments) is ok


def test_scope_follows_the_subagent() -> None:
    assert [
        scope_for(s) for s in ("research", "workload", "followup", "report", "briefing", None)
    ] == ["research", "workload", "followup", "report", "any", "any"]


def test_l2_is_queued_and_l1_is_not(session: Session, team: dict[str, str]) -> None:
    run = _run(session, team, "research", team["meeting"])
    l1 = ProposedAction(kind="k", title="t", tool="fake.level_one", level="L1", rationale="r")

    refused = queue_l2(
        session,
        actions={},
        run=run,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_1"), l1],
    )

    rows = session.scalars(select(AgentPendingAction)).all()
    assert refused == []
    assert [(r.tool, r.scope, r.status, r.arguments, r.run_id) for r in rows] == [
        ("agent.share_research_document", "research", "pending", {"document_id": "rdoc_1"}, run.id)
    ]


def test_free_text_is_refused_and_recorded(session: Session, team: dict[str, str]) -> None:
    run = _run(session, team, "workload", None)

    refused = queue_l2(
        session,
        actions={},
        run=run,
        proposed=[_l2("extraction.add_action_item", description="김 팀장 배포")],
    )

    assert session.scalars(select(AgentPendingAction)).all() == []
    assert refused == [
        {
            "tool": "extraction.add_action_item",
            "level": "L2",
            "ok": False,
            "reason": ARGUMENT_REFUSED,
            "evidence": [],
        }
    ]
    assert "김" not in repr(refused)


def test_a_newer_run_supersedes_the_same_subagents_pending_proposal(
    session: Session, team: dict[str, str]
) -> None:
    first = _run(session, team, "research", team["meeting"])
    queue_l2(
        session,
        actions={},
        run=first,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_1")],
    )
    second = _run(session, team, "research", team["meeting"])

    queue_l2(
        session,
        actions={},
        run=second,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_1")],
    )

    statuses = [
        (r.run_id, r.status)
        for r in session.scalars(select(AgentPendingAction).order_by(AgentPendingAction.created_at))
    ]
    assert sorted(s for _, s in statuses) == ["pending", "superseded"]
    assert dict(statuses)[second.id] == "pending"


def test_a_chat_replaces_the_pipelines_proposal_of_the_same_action(
    session: Session, team: dict[str, str]
) -> None:
    # #879: "다시 써줘" or "올려줘" on a meeting page proposes the same post the
    # pipeline left; keeping both leaves a dead or doubled card. The approver
    # still has one card to decide.
    event = _run(session, team, "report", team["meeting"])
    queue_l2(session, actions={}, run=event, proposed=[_l2("intelligence.publish_meeting_report")])
    chat = _run(session, team, "report", team["meeting"], trigger={"kind": "chat"})

    queue_l2(session, actions={}, run=chat, proposed=[_l2("intelligence.publish_meeting_report")])

    statuses = {r.run_id: r.status for r in session.scalars(select(AgentPendingAction))}
    assert statuses == {event.id: "superseded", chat.id: "pending"}


def test_a_chat_leaves_the_pipelines_proposal_of_another_action(
    session: Session, team: dict[str, str]
) -> None:
    # #651 review still holds for a different action: asking on a meeting page
    # must not retire what the pipeline left there for an approver.
    event = _run(session, team, "report", team["meeting"])
    queue_l2(session, actions={}, run=event, proposed=[_l2("intelligence.publish_meeting_report")])
    chat = _run(session, team, "report", team["meeting"], trigger={"kind": "chat"})

    queue_l2(
        session,
        actions={},
        run=chat,
        proposed=[_l2("intelligence.publish_meeting_report_correction", correction_id="rco_1")],
    )

    statuses = {r.run_id: r.status for r in session.scalars(select(AgentPendingAction))}
    assert statuses == {event.id: "pending", chat.id: "pending"}


def test_another_subagent_or_a_decided_row_is_not_superseded(
    session: Session, team: dict[str, str]
) -> None:
    research = _run(session, team, "research", team["meeting"])
    queue_l2(
        session,
        actions={},
        run=research,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_1")],
    )
    decided = session.scalars(select(AgentPendingAction)).one()
    decided.status = "approved"
    report = _run(session, team, "report", team["meeting"])

    queue_l2(
        session,
        actions={},
        run=report,
        proposed=[_l2("intelligence.publish_meeting_report", meeting_id=team["meeting"])],
    )
    research2 = _run(session, team, "research", team["meeting"])
    queue_l2(
        session,
        actions={},
        run=research2,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_2")],
    )

    by_tool = {(r.tool, r.status) for r in session.scalars(select(AgentPendingAction))}
    assert ("agent.share_research_document", "approved") in by_tool
    assert ("intelligence.publish_meeting_report", "pending") in by_tool


def test_a_proposal_about_no_meeting_supersedes_nothing(
    session: Session, team: dict[str, str]
) -> None:
    for _ in range(2):
        run = _run(session, team, "workload", None)
        queue_l2(
            session,
            actions={},
            run=run,
            proposed=[
                _l2("extraction.reassign_action_item", action_item_id="act_1", assignee_id="user_2")
            ],
        )

    assert [r.status for r in session.scalars(select(AgentPendingAction))] == ["pending", "pending"]


def test_two_proposals_of_one_run_both_stay_pending(session: Session, team: dict[str, str]) -> None:
    run = _run(session, team, "research", team["meeting"])

    queue_l2(
        session,
        actions={},
        run=run,
        proposed=[
            _l2("agent.share_research_document", document_id="rdoc_1"),
            _l2("agent.share_research_document", document_id="rdoc_2"),
        ],
    )

    assert [r.status for r in session.scalars(select(AgentPendingAction))] == ["pending", "pending"]


def _noop(**_: Any) -> dict[str, Any]:
    return {"ok": True, "summary": "."}


def test_a_proposal_marked_l1_whose_module_declared_l2_is_queued(
    session: Session, team: dict[str, str]
) -> None:
    """execute_l1 keeps it for approval; queue_l2 must not drop it."""
    run = _run(session, team, "workload", team["meeting"])
    demoted = ProposedAction(
        kind="reassign_action_item",
        title="t",
        tool="fake.post",
        arguments={"action_item_id": "act_1"},
        level="L1",
        rationale="r",
    )
    declared_l1 = ProposedAction(kind="k", title="t", tool="fake.note", level="L1", rationale="r")
    actions = {
        "fake.post": Action("fake.post", _noop, "L2"),
        "fake.note": Action("fake.note", _noop, "L1"),
    }

    refused = queue_l2(session, run=run, proposed=[demoted, declared_l1], actions=actions)

    rows = session.scalars(select(AgentPendingAction)).all()
    assert refused == []
    assert [(r.tool, r.status, r.arguments) for r in rows] == [
        ("fake.post", "pending", {"action_item_id": "act_1"})
    ]


@pytest.mark.parametrize("route", ["Research", "r" * 33])
def test_a_subagent_that_is_not_a_code_name_is_refused(
    session: Session, team: dict[str, str], route: str
) -> None:
    run = _run(session, team, route, team["meeting"])

    refused = queue_l2(
        session,
        run=run,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_1")],
        actions={},
    )

    assert session.scalars(select(AgentPendingAction)).all() == []
    assert [(r["ok"], r["reason"], r["level"]) for r in refused] == [
        (False, ARGUMENT_REFUSED, "L2")
    ]


def test_a_pending_row_whose_run_was_deleted_is_superseded(
    session: Session, team: dict[str, str]
) -> None:
    orphan = AgentPendingAction(
        team_id=team["team"],
        meeting_id=team["meeting"],
        run_id=None,
        subagent="research",
        tool="agent.share_research_document",
        kind="k",
        arguments={"document_id": "rdoc_1"},
        evidence=[],
        scope="research",
    )
    session.add(orphan)
    session.flush()
    run = _run(session, team, "research", team["meeting"])

    queue_l2(
        session,
        run=run,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_2")],
        actions={},
    )

    session.refresh(orphan)
    assert orphan.status == "superseded"


def test_a_run_queues_what_execute_l1_kept_for_approval(
    session: Session, team: dict[str, str]
) -> None:
    """run_and_record hands queue_l2 the same declared actions it gave execute_l1."""
    demoted = ProposedAction(
        kind="reassign_action_item",
        title="t",
        tool="fake.post",
        arguments={"action_item_id": "act_1"},
        level="L1",
        rationale="r",
    )

    def build(toolbox: Toolbox) -> Any:
        def act(state: SubagentState) -> SubagentState:
            result = ToolResult(ok=True, summary="제안합니다.")
            return {"outcome": SubagentResult(result=result, proposed=[demoted])}

        graph = StateGraph(SubagentState)
        graph.add_node("act", act)
        graph.add_edge(START, "act")
        graph.add_edge("act", END)
        return graph.compile()

    subagent = Subagent(name="workload", description="Use this in tests.", tools=(), build=build)
    calls: list[Any] = []

    def post(**arguments: Any) -> dict[str, Any]:
        calls.append(arguments)
        return {"ok": True, "summary": "."}

    row, _ = run_and_record(
        "업무",
        session=session,
        router=FakeRouter({"업무": "workload"}),
        team_id=team["team"],
        meeting_id=team["meeting"],
        trigger={"kind": "chat"},
        subagents={"workload": subagent},
        tools={},
        actions={"fake.post": Action("fake.post", post, "L2")},
    )

    assert calls == []  # never run at L1
    assert [a["reason"] for a in row.actions] == [KEPT_FOR_APPROVAL]
    queued = session.scalars(select(AgentPendingAction)).all()
    assert [(q.tool, q.status, q.run_id) for q in queued] == [("fake.post", "pending", row.id)]


def _another_meeting(session: Session, team: dict[str, str]) -> str:
    meeting = Meeting(team_id=team["team"], title="다른 회의")
    session.add(meeting)
    session.flush()
    return meeting.id


def _reassign(action_item: str = "act_1", assignee: str = "user_2") -> ProposedAction:
    return _l2("extraction.reassign_action_item", action_item_id=action_item, assignee_id=assignee)


def test_a_team_wide_run_supersedes_the_teams_proposals_from_other_meetings(
    session: Session, team: dict[str, str]
) -> None:
    """#631: Workload weighs the team, so meeting B's run replaces meeting A's proposal."""
    first = _run(session, team, "workload", team["meeting"])
    queue_l2(
        session, run=first, proposed=[_reassign(assignee="user_2")], actions={}, team_wide=True
    )
    second = _run(session, team, "workload", _another_meeting(session, team))

    queue_l2(
        session, run=second, proposed=[_reassign(assignee="user_3")], actions={}, team_wide=True
    )

    rows = {r.run_id: r.status for r in session.scalars(select(AgentPendingAction))}
    assert rows == {first.id: "superseded", second.id: "pending"}


def test_a_team_wide_run_about_no_meeting_supersedes_too(
    session: Session, team: dict[str, str]
) -> None:
    """Asked twice in chat, a team-wide subagent leaves one proposal, not two."""
    runs = []
    for _ in range(2):
        run = _run(session, team, "workload", None, trigger={"kind": "chat"})
        queue_l2(session, run=run, proposed=[_reassign()], actions={}, team_wide=True)
        runs.append(run)

    rows = {r.run_id: r.status for r in session.scalars(select(AgentPendingAction))}
    assert rows == {runs[0].id: "superseded", runs[1].id: "pending"}


def test_a_team_wide_chat_supersedes_what_the_timer_left(
    session: Session, team: dict[str, str]
) -> None:
    """A team-wide judgment replaces the last one, whoever asked (#651 review):
    otherwise the same item stays proposed to two people."""
    timer = _run(session, team, "workload", None, trigger={"kind": "periodic"})
    queue_l2(session, run=timer, proposed=[_reassign()], actions={}, team_wide=True)
    chat = _run(session, team, "workload", None, trigger={"kind": "chat"})

    queue_l2(session, run=chat, proposed=[_reassign()], actions={}, team_wide=True)

    rows = {r.run_id: r.status for r in session.scalars(select(AgentPendingAction))}
    assert rows == {timer.id: "superseded", chat.id: "pending"}


def test_a_chat_on_a_meeting_replaces_an_earlier_chat_and_the_same_pipeline_proposal(
    session: Session, team: dict[str, str]
) -> None:
    event = _run(session, team, "followup", team["meeting"])
    queue_l2(session, actions={}, run=event, proposed=[_l2("extraction.add_followup_item")])
    first = _run(session, team, "followup", team["meeting"], trigger={"kind": "chat"})
    queue_l2(session, actions={}, run=first, proposed=[_l2("extraction.add_followup_item")])
    second = _run(session, team, "followup", team["meeting"], trigger={"kind": "chat"})

    queue_l2(session, actions={}, run=second, proposed=[_l2("extraction.add_followup_item")])

    rows = {r.run_id: r.status for r in session.scalars(select(AgentPendingAction))}
    assert rows == {event.id: "superseded", first.id: "superseded", second.id: "pending"}


def test_a_team_wide_run_leaves_other_subagents_and_other_teams_alone(
    session: Session, team: dict[str, str]
) -> None:
    research = _run(session, team, "research", team["meeting"])
    queue_l2(
        session,
        run=research,
        proposed=[_l2("agent.share_research_document", document_id="rdoc_1")],
        actions={},
    )
    workload = _run(session, team, "workload", _another_meeting(session, team))

    queue_l2(session, run=workload, proposed=[_reassign()], actions={}, team_wide=True)

    assert {(r.subagent, r.status) for r in session.scalars(select(AgentPendingAction))} == {
        ("research", "pending"),
        ("workload", "pending"),
    }


def _proposing(name: str, proposed: ProposedAction, **declared: Any) -> Subagent:
    def build(toolbox: Toolbox) -> Any:
        def act(state: SubagentState) -> SubagentState:
            result = ToolResult(ok=True, summary="제안합니다.")
            return {"outcome": SubagentResult(result=result, proposed=[proposed])}

        graph = StateGraph(SubagentState)
        graph.add_node("act", act)
        graph.add_edge(START, "act")
        graph.add_edge("act", END)
        return graph.compile()

    return Subagent(name=name, description="Use this in tests.", tools=(), build=build, **declared)


@pytest.mark.parametrize(
    ("proposals_per", "first_status"), [("team", "superseded"), ("meeting", "pending")]
)
def test_a_run_reads_the_subagents_declaration(
    session: Session, team: dict[str, str], proposals_per: str, first_status: str
) -> None:
    """run_and_record passes the woken subagent's ``proposals_per`` to the queue."""
    subagent = _proposing("workload", _reassign(), proposals_per=proposals_per)
    rows = []
    for meeting in (team["meeting"], _another_meeting(session, team)):
        row, _ = run_and_record(
            "intelligence.completed",
            session=session,
            router=FakeRouter({}),
            team_id=team["team"],
            meeting_id=meeting,
            trigger={"kind": "event"},
            subagents={"workload": subagent},
            tools={},
            actions={},
            route_to="workload",
        )
        rows.append(row)

    statuses = {r.run_id: r.status for r in session.scalars(select(AgentPendingAction))}
    assert statuses == {rows[0].id: first_status, rows[1].id: "pending"}


def test_proposals_default_to_per_meeting() -> None:
    assert _proposing("research", _reassign()).proposals_per == "meeting"


def _publish(meeting: str) -> ProposedAction:
    return _l2("intelligence.publish_meeting_report", meeting_id=meeting, draft_id="rdr_1")


def test_a_team_chat_proposal_belongs_to_the_meeting_it_names(
    session: Session, team: dict[str, str]
) -> None:
    """#862: a chat on the team screen has no meeting; the proposal names one."""
    chat = _run(session, team, "report", None, trigger={"kind": "chat"})

    queue_l2(session, actions={}, run=chat, proposed=[_publish(team["meeting"])])

    row = session.scalars(select(AgentPendingAction)).one()
    assert row.meeting_id == team["meeting"]


def test_asking_again_from_the_team_screen_replaces_the_last_card(
    session: Session, team: dict[str, str]
) -> None:
    runs = []
    for _ in range(2):
        chat = _run(session, team, "report", None, trigger={"kind": "chat"})
        queue_l2(session, actions={}, run=chat, proposed=[_publish(team["meeting"])])
        runs.append(chat)

    rows = {r.run_id: r.status for r in session.scalars(select(AgentPendingAction))}
    assert rows == {runs[0].id: "superseded", runs[1].id: "pending"}


def test_a_team_chat_replaces_the_pipelines_same_proposal_for_that_meeting(
    session: Session, team: dict[str, str]
) -> None:
    """The meeting-page rule (#879) holds from the team screen too."""
    event = _run(session, team, "report", team["meeting"])
    queue_l2(session, actions={}, run=event, proposed=[_publish(team["meeting"])])
    chat = _run(session, team, "report", None, trigger={"kind": "chat"})

    queue_l2(session, actions={}, run=chat, proposed=[_publish(team["meeting"])])

    rows = {r.run_id: r.status for r in session.scalars(select(AgentPendingAction))}
    assert rows == {event.id: "superseded", chat.id: "pending"}


def test_a_team_chat_naming_another_teams_meeting_is_refused(
    session: Session, team: dict[str, str]
) -> None:
    other = Team(name="다른 팀")
    session.add(other)
    session.flush()
    foreign = Meeting(team_id=other.id, title="남의 회의")
    session.add(foreign)
    session.flush()
    chat = _run(session, team, "report", None, trigger={"kind": "chat"})

    refused = queue_l2(session, actions={}, run=chat, proposed=[_publish(foreign.id)])

    assert session.scalars(select(AgentPendingAction)).all() == []
    assert [r["reason"] for r in refused] == ["meeting not found"]


# --- a team-wide proposal is its item's meeting's (#959) ------------------------------


def _rows(session: Session) -> list[AgentPendingAction]:
    return list(session.scalars(select(AgentPendingAction)))


def test_a_team_wide_proposal_is_the_meeting_it_names_not_the_one_that_woke_the_run(
    session: Session, team: dict[str, str]
) -> None:
    """The approval card names the row's meeting (#854, #959). A team-wide
    subagent judges the team: the meeting that woke its run is not what a
    proposal is about, the item's meeting is, and the proposal names it."""
    items_meeting = _another_meeting(session, team)
    woken = _run(session, team, "workload", team["meeting"])
    proposal = _l2(
        "extraction.reassign_action_item",
        action_item_id="act_1",
        assignee_id="user_2",
        meeting_id=items_meeting,
    )

    assert queue_l2(session, run=woken, proposed=[proposal], actions={}, team_wide=True) == []

    (row,) = _rows(session)
    assert row.meeting_id == items_meeting
    assert (row.run_id, woken.meeting_id) == (woken.id, team["meeting"]), "the run is unchanged"


def test_a_team_wide_proposal_that_names_no_meeting_is_the_runs_as_before(
    session: Session, team: dict[str, str]
) -> None:
    woken = _run(session, team, "workload", team["meeting"])

    queue_l2(session, run=woken, proposed=[_reassign()], actions={}, team_wide=True)

    (row,) = _rows(session)
    assert row.meeting_id == team["meeting"]


def test_a_proposal_of_a_run_about_its_meeting_stays_that_meetings_whatever_it_names(
    session: Session, team: dict[str, str]
) -> None:
    """Not team-wide: the run is about its meeting, and so are its proposals.
    #959 changes nothing here."""
    named = _another_meeting(session, team)
    run = _run(session, team, "followup", team["meeting"])

    queue_l2(
        session,
        run=run,
        proposed=[_l2("extraction.add_followup_item", meeting_id=named)],
        actions={},
    )

    (row,) = _rows(session)
    assert row.meeting_id == team["meeting"]


def test_a_team_wide_proposal_cannot_name_a_meeting_outside_the_team(
    session: Session, team: dict[str, str]
) -> None:
    """The model writes a proposal's arguments. A woken run's own meeting is
    the team's by construction; a meeting the proposal names is checked, as it
    already is for a run about no meeting (#862)."""
    elsewhere = Team(name="다른 팀")
    session.add(elsewhere)
    session.flush()
    theirs = Meeting(team_id=elsewhere.id, title="남의 회의")
    session.add(theirs)
    session.flush()
    woken = _run(session, team, "workload", team["meeting"])

    refused = queue_l2(
        session,
        run=woken,
        proposed=[
            _l2("extraction.reassign_action_item", action_item_id="act_1", meeting_id=theirs.id),
            _l2("extraction.reassign_action_item", action_item_id="act_2", meeting_id="mtg_nobody"),
        ],
        actions={},
        team_wide=True,
    )

    assert [r["reason"] for r in refused] == [MEETING_NOT_FOUND, MEETING_NOT_FOUND]
    assert _rows(session) == []
