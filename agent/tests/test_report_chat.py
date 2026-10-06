"""The E agent's chat path over mock tools and a scripted model (no database)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from autune_agent.main import CallBudget, RunScope, Tool, Toolbox
from autune_agent.main.toolcall import FunctionCall
from autune_agent.results import SubagentResult
from autune_agent.subagents.report import SUBAGENT, chat
from autune_agent.subagents.report.graph import (
    ACTIONS_TOOL,
    AWAITING_TOOL,
    CORRECTION_ACTION,
    DRAFT_ACTION,
    PUBLISH_ACTION,
    REVIEW_TOOL,
    TRIGGERS,
)
from autune_agent.testing import mock_tool

TEAM = "team_a"
MEETING = "mtg_ab12cd"


class Script:
    """A ToolModel that returns the given steps in order, then "DONE"."""

    def __init__(self, *steps: list[FunctionCall] | str) -> None:
        self.steps = list(steps)
        self.seen: list[list[dict[str, Any]]] = []
        self.last_parts: list[dict[str, Any]] = []

    def step(self, instructions: str, turns: list[dict[str, Any]], declarations: list[Any]) -> Any:
        self.seen.append(list(turns))
        return self.steps.pop(0) if self.steps else "DONE"


class _Session:
    def get(self, _model: object, ident: str) -> Any:
        if ident.startswith("mtg_other"):
            return SimpleNamespace(team_id="team_b")
        return SimpleNamespace(team_id=TEAM) if ident.startswith("mtg_") else None


def call(name: str, **args: Any) -> FunctionCall:
    return FunctionCall(name.replace(".", "__"), args)


def _body(**over: Any) -> dict[str, Any]:
    item = {
        "title": "📋 결제 회의 · 10/2",
        "body": "본문",
        "id": MEETING,
        "status": "draft",
        "draft_id": "rdr_a",
        "editor": None,
    }
    item.update(over)
    return {"ok": True, "summary": "회의 리포트입니다.", "items": [item]}


def _per_meeting(name: str, result: dict[str, Any]) -> Tool:
    """A read with E's real shape: ``meeting_id`` is required, so a team-scoped run
    that passes none gets the Toolbox's ``NO_MEETING`` refusal -- the scope probe."""

    def fn(_session: object, team_id: str, meeting_id: str) -> dict[str, Any]:
        return dict(result)

    return Tool(name=name, description="Use this in tests.", fn=fn)


def _tools(
    body: dict[str, Any] | None = None, awaiting: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "intelligence.team_trend": mock_tool(
            "intelligence.team_trend", {"ok": True, "summary": "평균 C등급.", "items": []}
        ),
        "intelligence.explain_metric": mock_tool(
            "intelligence.explain_metric",
            {
                "ok": True,
                "summary": "E 지표 설명입니다.",
                "items": [{"title": "등급 기준", "body": "A 0.9 이상"}],
            },
        ),
        "intelligence.meeting_report_body": _per_meeting(
            "intelligence.meeting_report_body", body or _body()
        ),
        AWAITING_TOOL: _per_meeting(
            AWAITING_TOOL, awaiting or {"ok": True, "summary": "", "items": []}
        ),
        ACTIONS_TOOL: _per_meeting(
            ACTIONS_TOOL,
            {
                "ok": True,
                "summary": "확정 1건.",
                "items": [{"title": "API 스펙", "body": "백엔드 · 10/2"}],
            },
        ),
        REVIEW_TOOL: _per_meeting(REVIEW_TOOL, {"ok": True, "summary": "", "items": []}),
    }


def _run(
    request: str,
    model: Any,
    tools: dict[str, Any],
    *,
    meeting: str | None = MEETING,
    monkeypatch: pytest.MonkeyPatch,
) -> SubagentResult:
    monkeypatch.setattr(chat, "MODEL_FACTORY", lambda: model)
    box = Toolbox(
        tools,
        _Session(),
        CallBudget(),
        scope=RunScope(team_id=TEAM, meeting_id=meeting, user_id="usr_1"),
        allowed=SUBAGENT.tools,
    )  # type: ignore[arg-type]
    out = SUBAGENT.build(box).invoke({"request": request})
    return SubagentResult.model_validate(out["outcome"])


def test_a_why_question_is_answered_from_numbers_and_the_glossary(monkeypatch) -> None:
    model = Script(
        [call("intelligence.team_trend"), call("intelligence.explain_metric", question="등급 기준")]
    )
    out = _run("왜 우리 팀이 C등급이야?", model, _tools(), monkeypatch=monkeypatch)
    assert out.result.ok and "C등급" in out.result.summary and out.proposed == []
    assert any(i.title == "등급 기준" for i in out.result.items)


def test_request_post_in_a_meeting_proposes_the_stored_draft(monkeypatch) -> None:
    out = _run("리포트 올려줘", Script([call("request_post")]), _tools(), monkeypatch=monkeypatch)
    (post,) = out.proposed
    assert (post.tool, post.arguments.get("draft_id"), post.level) == (
        PUBLISH_ACTION,
        "rdr_a",
        "L2",
    )
    assert "요청했습니다" in out.result.summary


def test_request_post_in_the_team_view_proposes_nothing_and_points_to_the_meeting(
    monkeypatch,
) -> None:
    tools = _tools()
    out = _run(
        "리포트 올려줘",
        Script([call("request_post", meeting_id=MEETING)]),
        tools,
        meeting=None,
        monkeypatch=monkeypatch,
    )
    assert out.proposed == []
    assert "회의 화면에서" in out.result.summary


def test_request_post_with_a_waiting_correction_proposes_the_correction(monkeypatch) -> None:
    awaiting = {
        "ok": True,
        "summary": "",
        "items": [
            {
                "title": "리포트 수정본",
                "kind": "correction",
                "correction_id": "rcr_1",
                "id": MEETING,
            }
        ],
    }
    out = _run(
        "정정 올려줘",
        Script([call("request_post")]),
        _tools(body=_body(status="posted"), awaiting=awaiting),
        monkeypatch=monkeypatch,
    )
    (post,) = out.proposed
    assert (post.tool, post.arguments.get("correction_id")) == (CORRECTION_ACTION, "rcr_1")


def test_redraft_carries_the_draft_it_read(monkeypatch) -> None:
    out = _run(
        "최신 수치로 다시 써줘", Script([call("redraft")]), _tools(), monkeypatch=monkeypatch
    )
    draft = next(p for p in out.proposed if p.tool == DRAFT_ACTION)
    assert draft.arguments["replaces_draft_id"] == "rdr_a" and draft.level == "L1"


def test_redraft_leaves_a_members_edit_alone(monkeypatch) -> None:
    out = _run(
        "다시 써줘",
        Script([call("redraft")]),
        _tools(body=_body(editor="이승환")),
        monkeypatch=monkeypatch,
    )
    assert out.proposed == [] and "이승환님이 고친 초안" in out.result.summary


def test_redraft_of_a_posted_report_points_to_a_correction(monkeypatch) -> None:
    out = _run(
        "다시 써줘",
        Script([call("redraft")]),
        _tools(body=_body(status="posted")),
        monkeypatch=monkeypatch,
    )
    assert out.proposed == [] and "수정본" in out.result.summary


def test_redraft_with_no_stored_report_proposes_a_first_draft(monkeypatch) -> None:
    """Review Focus 5."""
    empty = {"ok": True, "summary": "이 회의에는 아직 리포트가 없습니다.", "items": []}
    out = _run(
        "리포트 써줘", Script([call("redraft")]), _tools(body=empty), monkeypatch=monkeypatch
    )
    draft = next(p for p in out.proposed if p.tool == DRAFT_ACTION)
    assert "replaces_draft_id" not in draft.arguments
    assert any(p.tool == PUBLISH_ACTION for p in out.proposed)  # meeting-scoped run


def test_the_schedule_change_is_held_back_until_862(monkeypatch) -> None:
    model = Script([call("intelligence.set_weekly_report_schedule", weekday=4, hour=18)])
    out = _run("금요일 6시로 바꿔줘", model, _tools(), monkeypatch=monkeypatch)
    assert out.proposed == []
    assert all("user_id" not in str(d) for d in chat.declarations())


def test_a_model_that_calls_nothing_or_an_unknown_tool_still_answers(monkeypatch) -> None:
    """Review Focus 3."""
    out = _run("요즘 어때?", Script("그냥 답"), _tools(), monkeypatch=monkeypatch)
    assert out.result.summary == "답할 내용을 찾지 못했습니다. 대시보드에서 확인해 주세요."

    out = _run("요즘 어때?", Script([call("intelligence.nope")]), _tools(), monkeypatch=monkeypatch)
    assert out.proposed == [] and "요청했습니다" not in out.result.summary
    assert "가져오지 못한" not in out.result.summary

    seen: list[str] = []

    def trend(_session: object, team_id: str) -> dict[str, Any]:
        seen.append(team_id)
        return {"ok": True, "summary": "평균 C등급.", "items": []}

    tools = _tools()
    tools["intelligence.team_trend"] = Tool(
        name="intelligence.team_trend", description="Use this in tests.", fn=trend
    )
    model = Script([call("intelligence.team_trend", team_id="team_other")])
    out = _run("요즘 어때?", model, tools, monkeypatch=monkeypatch)
    assert seen == [TEAM] and "C등급" in out.result.summary


def test_redraft_in_the_team_view_proposes_the_draft_and_no_post(monkeypatch) -> None:
    out = _run(
        "다시 써줘",
        Script([call("redraft", meeting_id=MEETING)]),
        _tools(),
        meeting=None,
        monkeypatch=monkeypatch,
    )
    assert [p.tool for p in out.proposed] == [DRAFT_ACTION]


@pytest.mark.parametrize("action", ["redraft", "request_post"])
def test_another_teams_meeting_proposes_nothing(monkeypatch, action) -> None:
    out = _run(
        "해줘", Script([call(action, meeting_id="mtg_other1")]), _tools(), monkeypatch=monkeypatch
    )
    assert out.proposed == []


def _posts(out: SubagentResult) -> list[Any]:
    return [p for p in out.proposed if p.tool == PUBLISH_ACTION]


def test_redraft_then_request_post_proposes_one_post_and_it_is_redrafts(monkeypatch) -> None:
    out = _run(
        "다시 써서 올려줘",
        Script([call("redraft"), call("request_post")]),
        _tools(),
        monkeypatch=monkeypatch,
    )
    (post,) = _posts(out)
    (draft,) = [p for p in out.proposed if p.tool == DRAFT_ACTION]
    assert post.arguments["draft_id"] == draft.arguments["draft_id"] != "rdr_a"
    assert "게시도 함께 요청했습니다." in out.result.summary


def test_request_post_then_redraft_replaces_the_earlier_post(monkeypatch) -> None:
    out = _run(
        "올리고 다시 써줘",
        Script([call("request_post"), call("redraft")]),
        _tools(),
        monkeypatch=monkeypatch,
    )
    (post,) = _posts(out)
    (draft,) = [p for p in out.proposed if p.tool == DRAFT_ACTION]
    assert post.arguments["draft_id"] == draft.arguments["draft_id"] != "rdr_a"


def test_each_action_runs_once_per_run(monkeypatch) -> None:
    out = _run(
        "다시 써줘", Script([call("redraft"), call("redraft")]), _tools(), monkeypatch=monkeypatch
    )
    assert len([p for p in out.proposed if p.tool == DRAFT_ACTION]) == 1
    assert out.result.summary.count("다시 만들도록 요청했습니다") == 1


def test_without_a_model_a_chat_request_runs_the_template_as_today(monkeypatch) -> None:
    out = _run("리포트 써줘", None, _tools(), monkeypatch=monkeypatch)
    assert {p.tool for p in out.proposed} == {DRAFT_ACTION, PUBLISH_ACTION}


@pytest.mark.parametrize("trigger", TRIGGERS)
def test_a_trigger_never_reaches_the_model(monkeypatch, trigger) -> None:
    model = Script()
    _run(trigger, model, _tools(), monkeypatch=monkeypatch)
    assert model.seen == []


def test_the_models_own_parts_with_their_signatures_are_echoed(monkeypatch) -> None:
    model = Script([call("intelligence.team_trend")])
    model.last_parts = [
        {
            "functionCall": {"name": "intelligence__team_trend", "args": {}},
            "thoughtSignature": "sig",
        }
    ]
    _run("요즘 어때?", model, _tools(), monkeypatch=monkeypatch)
    echoed = model.seen[1][1]["parts"]
    assert echoed[0]["thoughtSignature"] == "sig"


def test_a_request_over_the_limit_is_trimmed_not_ended(monkeypatch) -> None:
    big = {
        "ok": True,
        "summary": "본문.",
        "items": [
            {
                "title": "t",
                "body": "가" * 1500,
                "id": MEETING,
                "status": "draft",
                "draft_id": "rdr_a",
                "editor": None,
            }
        ],
    }
    explain = {
        "ok": True,
        "summary": "설명.",
        "items": [{"title": f"x{n}", "body": "나" * 400} for n in range(3)],
    }
    tools = _tools(body=big)
    tools["intelligence.explain_metric"] = mock_tool("intelligence.explain_metric", explain)
    model = Script(
        [call("intelligence.meeting_report_body")],
        [
            call("intelligence.explain_metric", question="a"),
            call("intelligence.explain_metric", question="b"),
        ],
    )
    out = _run("설명해줘", model, tools, monkeypatch=monkeypatch)
    assert len(model.seen) == 3
    first_response = model.seen[2][2]["parts"][0]["functionResponse"]["response"]
    assert all("body" not in item for item in first_response["items"])
    assert "본문." in out.result.summary  # the reply keeps the full results


def test_a_failed_read_is_named_without_its_reason(monkeypatch) -> None:
    def boom(_session: object, team_id: str) -> dict[str, Any]:
        return {"ok": False, "reason": "SECRET-REASON", "summary": ""}

    tools = _tools()
    tools["intelligence.team_trend"] = Tool(
        name="intelligence.team_trend", description="Use this in tests.", fn=boom
    )
    out = _run(
        "요즘 어때?", Script([call("intelligence.team_trend")]), tools, monkeypatch=monkeypatch
    )
    assert out.result.summary.endswith("가져오지 못한 정보가 있습니다: team_trend.")
    assert "SECRET-REASON" not in out.result.summary
