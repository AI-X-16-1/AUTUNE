"""The Follow-up subagent against mock tools
(agent/docs/specs/2026-09-30-followup-subagent-design.md section 8).

The mocks return what C's ``open_gaps`` and ``recurring_open_gaps`` (#546),
B's ``unresolved_questions`` and A's ``recent_meetings`` return, extra fields
included, so a change to those shapes shows up here.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from autune_agent.main import CallBudget, RunScope, collect_subagents
from autune_agent.main.registry import Tool, Toolbox
from autune_agent.subagents.followup import SUBAGENT, rules
from autune_agent.subagents.followup.graph import ADD_ITEM, OPEN, QUESTIONS, RECENT, RECURRING
from autune_contracts import INTELLIGENCE_COMPLETED

TEAM = "team_a"
MEETING = "mtg_now"


class FakeSession:
    """Answers the toolbox's one question: which team a meeting belongs to."""

    def get(self, _entity: Any, ident: str) -> Any:
        return SimpleNamespace(team_id=TEAM) if ident.startswith("mtg_") else None


def gap(gid: str, title: str, score: float, severity: str = "medium") -> dict[str, Any]:
    return {
        "id": gid,
        "title": title,
        "body": "질문?",
        "score": score,
        "severity": severity,
        "template_item_key": title,
        "topics": ["배포"],
    }


def result(*items: dict[str, Any], evidence: list[str] | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "summary": "결과",
        "items": list(items),
        "evidence": evidence if evidence is not None else [i["id"] for i in items],
    }


def questions(n: int) -> dict[str, Any]:
    return result(
        *[{"id": f"utt_{i}", "title": "질문", "body": "누가?", "score": 0.5} for i in range(n)]
    )


def tools_for(
    open_: dict[str, Any],
    recurring: dict[str, Any],
    asked: dict[str, Any],
    recent: dict[str, Any] | None = None,
) -> tuple[dict[str, Tool], list[tuple[str, dict[str, Any]]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    def recorder(name: str, payload: dict[str, Any]) -> Tool:
        def fn(_session: Any, team_id: str, meeting_id: str) -> dict[str, Any]:
            calls.append((name, {"team_id": team_id, "meeting_id": meeting_id}))
            return payload

        return Tool(name=name, description="Use this in tests.", fn=fn)

    def recent_fn(_session: Any, team_id: str, days: int = 30) -> dict[str, Any]:
        calls.append((RECENT, {"team_id": team_id}))
        return recent or result()

    tools = {
        OPEN: recorder(OPEN, open_),
        RECURRING: recorder(RECURRING, recurring),
        QUESTIONS: recorder(QUESTIONS, asked),
        RECENT: Tool(name=RECENT, description="Use this in tests.", fn=recent_fn),
    }
    return tools, calls


def invoke(
    tools: dict[str, Tool], *, meeting: str | None = MEETING, budget: CallBudget | None = None
) -> Any:
    box = Toolbox(
        tools,
        FakeSession(),  # type: ignore[arg-type]
        budget or CallBudget(),
        allowed=SUBAGENT.tools,
        scope=RunScope(team_id=TEAM, meeting_id=meeting),
    )
    return SUBAGENT.build(box).invoke({"request": INTELLIGENCE_COMPLETED})["outcome"]


RISK = gap("gap_r2", "리스크 미논의", 0.9, "high")
NEXT = gap("gap_n2", "다음 단계 미정", 0.7, "high")
DEP = gap("gap_d2", "의존성 미확인", 0.4)


def test_an_item_left_open_twice_is_proposed_as_a_follow_up() -> None:
    carried = result(RISK, DEP, evidence=["gap_r2", "gap_r1", "gap_d2", "gap_d1"])
    tools, _ = tools_for(result(RISK, NEXT, DEP), carried, questions(0))

    outcome = invoke(tools)

    (proposal,) = outcome.proposed
    assert (proposal.level, proposal.tool) == ("L2", ADD_ITEM)
    assert proposal.arguments == {"description": "후속 회의: 리스크 미논의, 의존성 미확인"}
    assert proposal.evidence == ["gap_r2", "gap_r1", "gap_d2", "gap_d1"]
    assert "직전 회의에 이어" in proposal.rationale
    assert outcome.result.ok


def test_a_heavy_meeting_with_a_question_is_proposed() -> None:
    tools, _ = tools_for(result(RISK, NEXT, DEP), result(), questions(1))

    (proposal,) = invoke(tools).proposed

    assert proposal.arguments["description"] == "후속 회의: 리스크 미논의, 다음 단계 미정"
    assert proposal.evidence == ["gap_r2", "gap_n2"]


def test_heavy_without_a_question_is_not_enough() -> None:
    tools, _ = tools_for(result(RISK, NEXT), result(), questions(0))

    outcome = invoke(tools)

    assert outcome.proposed == []
    assert outcome.result.ok
    assert "필요해 보이지 않습니다" in outcome.result.summary


def test_one_high_gap_is_not_heavy() -> None:
    tools, _ = tools_for(result(RISK, DEP), result(), questions(3))

    assert invoke(tools).proposed == []


def test_a_meeting_with_nothing_open_proposes_nothing() -> None:
    tools, _ = tools_for(result(), result(), questions(2))

    assert invoke(tools).proposed == []


def test_the_description_names_three_titles_at_most() -> None:
    many = [gap(f"gap_{i}", f"항목{i}", 1.0 - i / 10, "high") for i in range(5)]
    tools, _ = tools_for(result(*many), result(*many), questions(0))

    (proposal,) = invoke(tools).proposed

    assert proposal.arguments["description"] == "후속 회의: 항목0, 항목1, 항목2"


def test_a_trigger_run_reads_three_tools_scoped_to_its_meeting() -> None:
    tools, calls = tools_for(result(RISK), result(RISK), questions(0))

    invoke(tools)

    assert [name for name, _ in calls] == [OPEN, RECURRING, QUESTIONS]
    assert all(args == {"team_id": TEAM, "meeting_id": MEETING} for _, args in calls)


def test_a_trigger_proposal_leaves_team_and_meeting_to_the_run() -> None:
    tools, _ = tools_for(result(RISK), result(RISK), questions(0))

    (proposal,) = invoke(tools).proposed

    assert set(proposal.arguments) == {"description"}, "team_id and meeting_id are the run's"


def test_a_chat_run_picks_the_most_recent_analysed_meeting() -> None:
    recent = result(
        {"id": "x", "title": "예정", "score": 0, "meeting_id": "mtg_next", "status": "scheduled"},
        {"id": "y", "title": "지난", "score": 0, "meeting_id": "mtg_last", "status": "complete"},
        evidence=["mtg_next", "mtg_last"],
    )
    tools, calls = tools_for(result(RISK), result(RISK), questions(0), recent)

    outcome = invoke(tools, meeting=None)

    assert [name for name, _ in calls] == [RECENT, OPEN, RECURRING, QUESTIONS]
    assert {args["meeting_id"] for name, args in calls if name != RECENT} == {"mtg_last"}
    (proposal,) = outcome.proposed
    assert proposal.arguments["meeting_id"] == "mtg_last"


def test_a_chat_run_with_no_analysed_meeting_stops() -> None:
    tools, _ = tools_for(result(), result(), questions(0), result())

    outcome = invoke(tools, meeting=None)

    assert not outcome.result.ok
    assert outcome.proposed == []


def test_a_failed_read_ends_without_a_proposal() -> None:
    broken = {"ok": False, "reason": "no gap analysis yet", "summary": "아직", "items": []}
    for tools, _ in (
        tools_for(broken, result(RISK), questions(0)),
        tools_for(result(RISK), broken, questions(0)),
        tools_for(result(RISK), result(RISK), broken),
    ):
        outcome = invoke(tools)
        assert not outcome.result.ok
        assert outcome.proposed == []


def test_it_asks_for_nothing_personal_and_wakes_on_intelligence_completed() -> None:
    assert SUBAGENT.triggers == (INTELLIGENCE_COMPLETED,)
    assert SUBAGENT.tools == (OPEN, RECURRING, QUESTIONS, RECENT)
    assert "followup" in collect_subagents()


def test_the_rule_prefers_carried_over() -> None:
    high = [rules.Gap("gap_a", "a", 0.9, "high"), rules.Gap("gap_b", "b", 0.8, "high")]
    carried = [rules.Gap("gap_c", "c", 0.3, "low")]

    decision = rules.decide(high, carried, [], questions_raised=1)

    assert decision is not None and decision.reason == "carried_over"
    assert decision.evidence == ("gap_c",)
