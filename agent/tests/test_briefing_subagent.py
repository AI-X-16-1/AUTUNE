"""The Briefing subagent end to end over mock tools (no database)."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import pytest

from autune_agent.main import BudgetExceededError, CallBudget, RunScope, Tool, Toolbox
from autune_agent.main.registry import collect_tools
from autune_agent.main.subagents import collect_subagents
from autune_agent.results import SubagentResult
from autune_agent.subagents.briefing import SUBAGENT
from autune_agent.subagents.briefing.graph import (
    ACTIONS_TOOL,
    AGENDA_TOOL,
    GAPS_TOOL,
    NOT_COMPOSED,
    RECAP_TOOL,
)
from autune_agent.subagents.briefing.render import MORE
from autune_agent.testing import mock_tool

TEAM = "team_a"
MEETING = "mtg_ab12cd"
PAST = "mtg_pa5700"

RECAP = {
    "ok": True,
    "summary": "이 회의는 지난 회의의 결정 2건을 이어받습니다.",
    "items": [
        {
            "title": "지난 주간 회의",
            "body": "2026-09-23 · 같은 제목의 지난 회의 · 주제: 검색 정렬",
            "kind": "previous_meeting",
            "meeting_id": PAST,
        },
        {"title": "검색 정렬은 관련도순으로 한다 (번복)", "kind": "decision"},
        {"title": "배포는 금요일에 한다", "kind": "decision"},
    ],
    "evidence": [PAST],
}
AGENDA = {
    "ok": True,
    "summary": "이 회의가 다룰 열린 Jira 이슈 1건.",
    "items": [{"title": "결제 모듈 API 명세 정리", "body": "AUT-1 · 진행 중"}],
}
ACTIONS = {
    "ok": True,
    "summary": "진행 중인 할 일 4건 중 기한 지남 1건.",
    "items": [{"title": "API 스펙 작성", "body": "백엔드 · 2026-09-30 · 기한 지남 · todo"}],
    "evidence": ["utt_a1"],
}
GAPS = {
    "ok": True,
    "summary": "열린 갭 2건, 그중 높음 1건.",
    "items": [
        {"title": "담당자와 기한", "body": "누가 언제까지 하나요?", "severity": "high"},
        {"title": "회고", "body": "다음에 무엇을 바꿀까요?", "severity": "low"},
    ],
    "evidence": ["gap_1"],
}


class _Session:
    """What the Toolbox's scope check reads: a meeting and its team."""

    def __init__(self, meetings: dict[str, str]) -> None:
        self.meetings = meetings

    def get(self, _model: object, ident: str) -> Any:
        team = self.meetings.get(ident)
        return None if team is None else SimpleNamespace(team_id=team)


class _Spy:
    """A tool with the real signature, so the Toolbox binds scope the way it does
    for a module's tool, and that records what it was called with."""

    def __init__(self, name: str, result: Mapping[str, Any], *, per_meeting: bool) -> None:
        self.calls: list[dict[str, Any]] = []
        payload = dict(result)

        def with_meeting(_session: Any, team_id: str, meeting_id: str) -> Mapping[str, Any]:
            self.calls.append({"team_id": team_id, "meeting_id": meeting_id})
            return payload

        def team_only(_session: Any, team_id: str) -> Mapping[str, Any]:
            self.calls.append({"team_id": team_id})
            return payload

        self.tool = Tool(
            name=name,
            description="Use this in tests.",
            fn=with_meeting if per_meeting else team_only,
        )


def _spies(**overrides: Mapping[str, Any]) -> dict[str, _Spy]:
    results = {"recap": RECAP, "agenda": AGENDA, "gaps": GAPS, "actions": ACTIONS, **overrides}
    return {
        "recap": _Spy(RECAP_TOOL, results["recap"], per_meeting=True),
        "agenda": _Spy(AGENDA_TOOL, results["agenda"], per_meeting=True),
        "gaps": _Spy(GAPS_TOOL, results["gaps"], per_meeting=True),
        "actions": _Spy(ACTIONS_TOOL, results["actions"], per_meeting=False),
    }


def _registry(spies: Mapping[str, _Spy]) -> dict[str, Tool]:
    return {spy.tool.name: spy.tool for spy in spies.values()}


def _run(
    request: str,
    tools: Mapping[str, Tool],
    *,
    scope_meeting: str | None = None,
    budget: CallBudget | None = None,
) -> SubagentResult:
    session = _Session({MEETING: TEAM, PAST: TEAM, "mtg_other1": "team_b"})
    box = Toolbox(
        tools,
        session,  # type: ignore[arg-type]
        budget or CallBudget(),
        scope=RunScope(team_id=TEAM, meeting_id=scope_meeting),
        allowed=SUBAGENT.tools,
    )
    out = SUBAGENT.build(box).invoke({"request": request})
    return SubagentResult.model_validate(out["outcome"])


def _sections(outcome: SubagentResult) -> dict[str, str]:
    return {item.title: item.body for item in outcome.result.items}


def test_a_meeting_becomes_four_sections_in_a_fixed_order_and_proposes_nothing() -> None:
    outcome = _run("이 회의 브리프", _registry(_spies()), scope_meeting=MEETING)

    assert outcome.result.ok
    assert [item.title for item in outcome.result.items] == [
        "지난 회의에서 이어받는 결정",
        "팀의 열린 Jira 이슈",
        "기한이 지났거나 다가온 할 일",
        "지난 회의에서 닫히지 않은 갭",
    ]
    assert outcome.proposed == []
    assert "'지난 주간 회의'를 이어받습니다" in outcome.result.summary
    assert "읽지 못한" not in outcome.result.summary


def test_the_sections_read_what_each_module_returned() -> None:
    sections = _sections(_run("브리프", _registry(_spies()), scope_meeting=MEETING))

    recap = sections["지난 회의에서 이어받는 결정"]
    assert recap.splitlines()[0] == (
        "지난 주간 회의 · 2026-09-23 · 같은 제목의 지난 회의 · 주제: 검색 정렬"
    )
    assert "• 검색 정렬은 관련도순으로 한다 (번복)" in recap
    assert sections["팀의 열린 Jira 이슈"] == "• 결제 모듈 API 명세 정리 — AUT-1 · 진행 중"
    assert (
        "• API 스펙 작성 — 백엔드 · 2026-09-30 · 기한 지남 · todo"
        in (sections["기한이 지났거나 다가온 할 일"])
    )
    gaps = sections["지난 회의에서 닫히지 않은 갭"]
    assert "• 담당자와 기한\n  ↳ 누가 언제까지 하나요?" in gaps
    assert "회고" not in gaps  # LOW stays behind C's toggle


def test_the_gap_read_is_about_the_earlier_meeting_not_this_one() -> None:
    spies = _spies()
    _run("브리프", _registry(spies), scope_meeting=MEETING)

    assert spies["gaps"].calls == [{"team_id": TEAM, "meeting_id": PAST}]
    assert spies["recap"].calls == [{"team_id": TEAM, "meeting_id": MEETING}]
    assert spies["agenda"].calls == [{"team_id": TEAM, "meeting_id": MEETING}]
    assert spies["actions"].calls == [{"team_id": TEAM}]


def test_a_chat_request_names_the_meeting_and_a_korean_particle_does_not_hide_it() -> None:
    spies = _spies()

    outcome = _run(f"{MEETING}의 브리프 만들어줘", _registry(spies))

    assert outcome.result.ok
    assert spies["recap"].calls == [{"team_id": TEAM, "meeting_id": MEETING}]


def test_a_brief_not_composed_yet_says_the_link_is_unknown_and_skips_the_gap_read() -> None:
    spies = _spies(recap={"ok": False, "reason": NOT_COMPOSED, "summary": "아직입니다."})

    outcome = _run("브리프", _registry(spies), scope_meeting=MEETING)

    sections = _sections(outcome)
    assert "10분 전에 정해집니다" in sections["지난 회의"]
    assert "아직 정해지지 않았습니다" in outcome.result.summary
    assert spies["gaps"].calls == []
    # The other halves still go; skipping the gap read is not "could not read".
    assert "팀의 열린 Jira 이슈" in sections
    assert "읽지 못한" not in outcome.result.summary


def test_a_tool_that_answered_none_keeps_its_section_in_its_own_words() -> None:
    empty = {"ok": True, "summary": "이 회의에 연결된 Jira 이슈가 없습니다.", "items": []}

    outcome = _run("브리프", _registry(_spies(agenda=empty)), scope_meeting=MEETING)

    assert _sections(outcome)["팀의 열린 Jira 이슈"] == "이 회의에 연결된 Jira 이슈가 없습니다."


def test_a_missing_tool_drops_its_section_and_the_summary_says_what_was_not_read() -> None:
    spies = _spies()
    registry = _registry(spies)
    del registry[AGENDA_TOOL]

    outcome = _run("브리프", registry, scope_meeting=MEETING)

    assert "팀의 열린 Jira 이슈" not in _sections(outcome)
    assert outcome.result.ok
    assert "읽지 못한 부분: Jira 안건." in outcome.result.summary


def test_a_tool_that_failed_is_not_written_as_none() -> None:
    spies = _spies(actions={"ok": False, "reason": "boom", "summary": "실패"})

    outcome = _run("브리프", _registry(spies), scope_meeting=MEETING)

    assert "기한이 지났거나 다가온 할 일" not in _sections(outcome)
    assert "읽지 못한 부분: 할 일." in outcome.result.summary


def test_a_tool_that_raises_drops_only_its_section() -> None:
    def boom(_session: Any, team_id: str) -> Mapping[str, Any]:
        raise RuntimeError(f"bug for {team_id}")

    registry = _registry(_spies())
    registry[ACTIONS_TOOL] = Tool(name=ACTIONS_TOOL, description="Use this in tests.", fn=boom)

    outcome = _run("브리프", registry, scope_meeting=MEETING)

    assert outcome.result.ok
    assert "기한이 지났거나 다가온 할 일" not in _sections(outcome)
    assert "지난 회의에서 이어받는 결정" in _sections(outcome)


def test_running_out_of_tool_calls_is_not_swallowed_as_a_missing_section() -> None:
    with pytest.raises(BudgetExceededError):
        _run("브리프", _registry(_spies()), scope_meeting=MEETING, budget=CallBudget(limit=2))


def test_a_meeting_d_cannot_find_is_a_failure_not_an_empty_brief() -> None:
    spies = _spies(recap={"ok": False, "reason": "meeting mtg_x not found", "summary": "없음"})

    outcome = _run("브리프", _registry(spies), scope_meeting=MEETING)

    assert not outcome.result.ok
    assert outcome.result.items == []
    assert spies["agenda"].calls == [] and spies["actions"].calls == []


def test_a_run_about_no_meeting_gets_the_toolboxs_refusal() -> None:
    outcome = _run("브리프 만들어줘", _registry(_spies()))

    assert not outcome.result.ok
    assert outcome.result.reason == "this run is about no meeting; pass meeting_id"


def test_two_different_meeting_ids_are_refused_not_guessed() -> None:
    outcome = _run(f"{MEETING} 와 {PAST} 브리프", _registry(_spies()))

    assert not outcome.result.ok
    assert outcome.result.reason == "the request names several meetings"


def test_another_teams_meeting_reads_as_missing() -> None:
    outcome = _run("mtg_other1 브리프", _registry(_spies()))

    assert not outcome.result.ok
    assert outcome.result.reason == "meeting not found"


def test_a_cut_section_says_there_is_more_and_the_result_says_it_was_cut() -> None:
    cut = {**ACTIONS, "truncated": True}

    outcome = _run("브리프", _registry(_spies(actions=cut)), scope_meeting=MEETING)

    assert _sections(outcome)["기한이 지났거나 다가온 할 일"].endswith(MORE)
    assert outcome.result.truncated is True


def test_evidence_is_every_tools_ids_once() -> None:
    outcome = _run("브리프", _registry(_spies()), scope_meeting=MEETING)

    assert outcome.result.evidence == [PAST, "utt_a1", "gap_1"]


def test_it_does_not_wake_on_a_timer_or_an_event() -> None:
    """Nothing it returns is posted, and a woken run's text is not stored."""
    assert SUBAGENT.triggers == ()


def test_the_allow_list_is_exactly_the_four_reads() -> None:
    assert SUBAGENT.name == "briefing"
    assert set(SUBAGENT.tools) == {RECAP_TOOL, AGENDA_TOOL, GAPS_TOOL, ACTIONS_TOOL}
    # Unreviewed model labels, and speech: neither belongs in a brief.
    assert "extraction.unresolved_questions" not in SUBAGENT.tools
    assert SUBAGENT.description.startswith("Use this")
    assert "never posts" in SUBAGENT.description


def test_every_tool_it_names_is_one_a_module_actually_ships() -> None:
    """An unregistered name is skipped silently, so a wrong one would lose a section."""
    shipped = collect_tools(["context", "extraction", "gap"])
    assert set(SUBAGENT.tools) <= set(shipped)


def test_its_not_composed_reason_is_the_one_d_returns() -> None:
    from autune_context.tools import BRIEF_NOT_COMPOSED

    assert NOT_COMPOSED == BRIEF_NOT_COMPOSED


def test_the_main_agent_collects_it() -> None:
    assert "briefing" in collect_subagents()


def test_a_mock_registry_with_real_shapes_is_enough_to_run_it() -> None:
    """The shape the docs promise: build against ``mock_tool`` before modules are ready."""
    registry = {
        name: mock_tool(name, {"ok": True, "summary": "없음", "items": []})
        for name in (RECAP_TOOL, AGENDA_TOOL, GAPS_TOOL, ACTIONS_TOOL)
    }

    outcome = _run("브리프", registry, scope_meeting=MEETING)

    assert outcome.result.ok
