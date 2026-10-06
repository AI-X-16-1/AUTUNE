"""``/api/agent``: members only, one row per turn, and a clear answer when the layer is off."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autune_agent import router as routes
from autune_agent.models import AgentApprover, AgentPendingAction, AgentResearchDocument, AgentRun
from autune_agent.testing import FakeRouter, ScriptedToolModel
from autune_core import AutuneError, Meeting, Team, User, current_user, get_session


def _client(
    session: Session,
    user_id: str,
    *,
    chat_router: object | None,
    tool_model: object | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/agent")

    @app.exception_handler(AutuneError)
    async def _error(_: object, exc: AutuneError) -> object:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[current_user] = lambda: session.get(User, user_id)
    if chat_router is not None:
        app.dependency_overrides[routes.get_chat_router] = lambda: chat_router
    app.dependency_overrides[routes.get_chat_tool_model] = lambda: tool_model
    return TestClient(app)


@pytest.fixture
def member(session: Session, team: dict[str, str]) -> Iterator[TestClient]:
    yield _client(session, team["member"], chat_router=FakeRouter())


def test_a_member_chats_and_the_turn_is_recorded(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    # No subagent is built yet, so every turn is unrouted -- and still recorded.
    reply = member.post("/api/agent/chat", json={"team_id": team["team"], "message": "안녕"})

    assert reply.status_code == 200
    body = reply.json()
    assert body["outcome"] == "unrouted"
    assert body["answer"] == "no subagent fits this request"
    run = session.get(AgentRun, body["run_id"])
    assert run is not None
    assert run.requested_by == team["member"]
    assert run.trigger == {"kind": "chat"}


def test_a_chat_from_a_meeting_page_is_scoped_to_that_meeting(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    # S34 sends the meeting the person is looking at; the run is bound to it,
    # the way a triggered run is bound to its event's meeting.
    reply = member.post(
        "/api/agent/chat",
        json={"team_id": team["team"], "meeting_id": team["meeting"], "message": "이 회의"},
    )

    assert reply.status_code == 200
    run = session.get(AgentRun, reply.json()["run_id"])
    assert run is not None and run.meeting_id == team["meeting"]


def test_a_meeting_of_another_team_is_refused_as_missing(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    other_team = Team(name="다른 팀")
    session.add(other_team)
    session.flush()
    elsewhere = Meeting(team_id=other_team.id, title="남의 회의")
    session.add(elsewhere)
    session.commit()

    reply = member.post(
        "/api/agent/chat",
        json={"team_id": team["team"], "meeting_id": elsewhere.id, "message": "이 회의"},
    )

    assert reply.status_code == 404
    assert elsewhere.id not in reply.text
    assert session.query(AgentRun).count() == 0


def test_a_non_member_is_refused(session: Session, team: dict[str, str]) -> None:
    outsider = _client(session, team["outsider"], chat_router=FakeRouter())

    reply = outsider.post("/api/agent/chat", json={"team_id": team["team"], "message": "안녕"})

    assert reply.status_code == 403
    assert session.query(AgentRun).count() == 0


def test_a_pasted_transcript_is_refused(member: TestClient, team: dict[str, str]) -> None:
    reply = member.post("/api/agent/chat", json={"team_id": team["team"], "message": "가" * 1001})

    assert reply.status_code == 422


def test_the_timeline_lists_the_team_runs_newest_first(
    member: TestClient, team: dict[str, str]
) -> None:
    for message in ("하나", "둘"):
        member.post("/api/agent/chat", json={"team_id": team["team"], "message": message})

    runs = member.get("/api/agent/runs", params={"team_id": team["team"]}).json()

    assert len(runs) == 2
    assert {r["outcome"] for r in runs} == {"unrouted"}
    assert "answer" not in runs[0]


def test_without_a_key_the_layer_says_so(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTUNE_AGENT_LLM_API_KEY", "")
    routes.get_agent_settings.cache_clear()
    client = _client(session, team["member"], chat_router=None)

    reply = client.post("/api/agent/chat", json={"team_id": team["team"], "message": "안녕"})

    assert reply.status_code == 500
    assert "AUTUNE_AGENT_LLM_API_KEY" in reply.text
    routes.get_agent_settings.cache_clear()


def _doc(session: Session, team: dict[str, str], status: str) -> str:
    doc = AgentResearchDocument(
        team_id=team["team"], meeting_id=team["meeting"], body=f"{status} 본문", status=status
    )
    session.add(doc)
    session.commit()
    return doc.id


def _research(client: TestClient, team: dict[str, str]) -> Any:
    return client.get(
        "/api/agent/research", params={"team_id": team["team"], "meeting_id": team["meeting"]}
    )


def test_a_member_sees_approved_documents_only(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    _doc(session, team, "approved")
    _doc(session, team, "proposed")

    assert [d["status"] for d in _research(member, team).json()] == ["approved"]


def test_a_research_approver_also_sees_proposals(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["member"], scope="research"))
    session.commit()
    _doc(session, team, "proposed")

    assert [d["status"] for d in _research(member, team).json()] == ["proposed"]


def test_research_is_refused_to_a_non_member(session: Session, team: dict[str, str]) -> None:
    outsider = _client(session, team["outsider"], chat_router=FakeRouter())

    assert _research(outsider, team).status_code == 403


def _queue(
    session: Session, team: dict[str, str], scope: str = "research", meeting_id: str | None = None
) -> AgentPendingAction:
    meeting_id = meeting_id or team["meeting"]
    doc = AgentResearchDocument(team_id=team["team"], meeting_id=meeting_id, body="본문")
    session.add(doc)
    session.flush()
    row = AgentPendingAction(
        team_id=team["team"],
        meeting_id=meeting_id,
        subagent="research",
        tool="agent.share_research_document",
        kind="research_share",
        arguments={"document_id": doc.id},
        evidence=[doc.id],
        scope=scope,
    )
    session.add(row)
    session.commit()
    return row


def test_an_approver_lists_pending_with_previews(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["member"], scope="research"))
    _queue(session, team)

    got = member.get("/api/agent/pending").json()

    assert [(p["tool"], p["title"], p["body"], p["needs_check"]) for p in got] == [
        ("agent.share_research_document", "리서치 문서 공유", "본문", False)
    ]


def _interrupted(session: Session, team: dict[str, str]) -> AgentPendingAction:
    """Claimed, then something raised before the outcome was written (pending.approve)."""
    row = _queue(session, team)
    row.status, row.decided_by, row.result_ok = "approved", team["member"], None
    session.commit()
    return row


def test_an_interrupted_approval_lists_as_needing_a_check(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["member"], scope="research"))
    row = _interrupted(session, team)
    # Another meeting: a meeting holds one proposed document at a time.
    other = Meeting(team_id=team["team"], title="다른 회의")
    session.add(other)
    session.flush()
    finished = _queue(session, team, meeting_id=other.id)
    finished.status, finished.result_ok = "approved", True
    session.commit()

    got = member.get("/api/agent/pending").json()

    assert [(p["id"], p["status"], p["needs_check"]) for p in got] == [(row.id, "approved", True)]


def test_an_interrupted_approval_is_not_listed_to_a_non_approver(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["member"], scope="workload"))
    _interrupted(session, team)

    assert member.get("/api/agent/pending").json() == []
    assert member.get("/api/agent/pending", params={"team_id": team["team"]}).json() == []


def test_a_member_who_is_no_approver_gets_an_empty_list(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    _queue(session, team)

    assert member.get("/api/agent/pending").json() == []


def test_an_approver_row_of_a_non_member_lists_nothing(
    session: Session, team: dict[str, str]
) -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["outsider"], scope="any"))
    _queue(session, team)
    outsider = _client(session, team["outsider"], chat_router=FakeRouter())

    assert outsider.get("/api/agent/pending").json() == []


def test_approving_shares_the_document(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["member"], scope="any"))
    row = _queue(session, team)

    reply = member.post(f"/api/agent/pending/{row.id}/approve")

    assert reply.status_code == 200 and reply.json()["status"] == "approved"
    doc = session.get(AgentResearchDocument, row.arguments["document_id"])
    session.refresh(doc)
    assert doc.status == "approved"


def test_deciding_twice_is_409_and_an_unknown_id_is_404(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["member"], scope="any"))
    row = _queue(session, team)
    member.post(f"/api/agent/pending/{row.id}/reject", json={"reason": "not_now"})

    assert member.post(f"/api/agent/pending/{row.id}/approve").status_code == 409
    missing = member.post("/api/agent/pending/pa_nobody/approve")
    assert missing.status_code == 404 and "pa_nobody" not in missing.text


def test_an_approver_who_lost_the_scope_is_refused(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    approver = AgentApprover(team_id=team["team"], user_id=team["member"], scope="workload")
    session.add(approver)
    row = _queue(session, team, scope="research")

    assert member.post(f"/api/agent/pending/{row.id}/approve").status_code == 403


def test_a_free_text_reason_is_422(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["member"], scope="any"))
    row = _queue(session, team)

    assert (
        member.post(
            f"/api/agent/pending/{row.id}/reject", json={"reason": "김 팀장 싫음"}
        ).status_code
        == 422
    )


def test_one_row_whose_preview_raises_still_lists_beside_a_good_row(
    member: TestClient,
    session: Session,
    team: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["member"], scope="any"))
    _queue(session, team)
    bad = AgentPendingAction(
        team_id=team["team"],
        meeting_id=team["meeting"],
        subagent="extraction",
        tool="extraction.reassign_action_item",
        kind="reassign",
        arguments={"action_item_id": "ai_x", "assignee_id": team["member"]},
        evidence=[],
        scope="any",
    )
    session.add(bad)
    session.commit()

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("secret detail")

    monkeypatch.setattr(routes, "collect_tools", lambda: {"extraction.action_item_status": boom})

    got = member.get("/api/agent/pending").json()

    assert {p["body"] for p in got} == {"본문", "미리보기를 만들지 못했습니다"}
    assert {p["title"] for p in got} == {"리서치 문서 공유", "reassign"}


def test_a_meeting_alone_names_the_team(
    member: TestClient, session: Session, team: dict[str, str]
) -> None:
    # #651 review: a person in two teams asks on the second team's meeting page;
    # the shell cannot know which team that is, the meeting does.
    reply = member.post(
        "/api/agent/chat", json={"meeting_id": team["meeting"], "message": "이 회의"}
    )

    assert reply.status_code == 200
    run = session.get(AgentRun, reply.json()["run_id"])
    assert run is not None
    assert (run.team_id, run.meeting_id) == (team["team"], team["meeting"])


def test_a_meeting_of_a_team_the_caller_is_not_in_reads_as_missing(
    session: Session, team: dict[str, str]
) -> None:
    outsider = _client(session, team["outsider"], chat_router=FakeRouter())

    reply = outsider.post("/api/agent/chat", json={"meeting_id": team["meeting"], "message": "x"})

    assert reply.status_code == 404
    assert team["meeting"] not in reply.text
    assert session.query(AgentRun).count() == 0


def test_neither_a_team_nor_a_meeting_is_422(member: TestClient) -> None:
    assert member.post("/api/agent/chat", json={"message": "x"}).status_code == 422


def test_queued_counts_what_the_run_actually_left_waiting(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # #651 review: proposed - executed also counted failed L1 and refused L2.
    real = routes.run_and_record

    def queueing(*args: Any, **kwargs: Any) -> Any:
        row, state = real(*args, **kwargs)
        waiting = _queue(session, team)
        waiting.run_id = row.id
        session.commit()
        return row, state

    monkeypatch.setattr(routes, "run_and_record", queueing)
    client = _client(session, team["member"], chat_router=FakeRouter())

    body = client.post("/api/agent/chat", json={"team_id": team["team"], "message": "x"}).json()

    assert body["queued"] == 1


def _asking_client(session: Session, user_id: str, model: object) -> TestClient:
    return _client(session, user_id, chat_router=FakeRouter(), tool_model=model)


def test_a_free_question_is_answered_from_tools(session: Session, team: dict[str, str]) -> None:
    # The model asks for nothing: module tools need their own tables, which the
    # unit suite's SQLite does not build. test_ask.py covers the rounds.
    model = ScriptedToolModel(["DONE"])

    reply = _asking_client(session, team["member"], model).post(
        "/api/agent/chat", json={"team_id": team["team"], "message": "최근 회의?"}
    )

    assert reply.status_code == 200
    assert reply.json()["route"] == "ask"
    assert reply.json()["pending"] == []


def test_without_a_tool_model_the_chat_answers_as_before(
    member: TestClient, team: dict[str, str]
) -> None:
    reply = member.post("/api/agent/chat", json={"team_id": team["team"], "message": "안녕"})

    assert reply.json()["outcome"] == "unrouted"
    assert reply.json()["pending"] == []


def _queue_from_run(session: Session, team: dict[str, str], run_id: str) -> AgentPendingAction:
    row = _queue(session, team)
    row.run_id = run_id
    session.commit()
    return row


def test_pending_lists_the_runs_proposals_for_an_approver_only(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["member"], scope="research"))
    session.commit()
    real = routes.run_and_record

    def recording(*args: Any, **kwargs: Any) -> Any:
        row, state = real(*args, **kwargs)
        _queue_from_run(session, team, row.id)
        return row, state

    monkeypatch.setattr(routes, "run_and_record", recording)
    approver = _client(session, team["member"], chat_router=FakeRouter())

    got = approver.post("/api/agent/chat", json={"team_id": team["team"], "message": "x"}).json()

    assert [(p["tool"], p["status"]) for p in got["pending"]] == [
        ("agent.share_research_document", "pending")
    ]


def test_pending_is_empty_for_a_member_who_cannot_decide(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = routes.run_and_record

    def recording(*args: Any, **kwargs: Any) -> Any:
        row, state = real(*args, **kwargs)
        _queue_from_run(session, team, row.id)
        return row, state

    monkeypatch.setattr(routes, "run_and_record", recording)
    member = _client(session, team["member"], chat_router=FakeRouter())

    got = member.post("/api/agent/chat", json={"team_id": team["team"], "message": "x"}).json()

    assert got["pending"] == []


def test_pending_uses_the_meetings_team_when_only_a_meeting_is_sent(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The approver check must use the team the meeting named, not the absent team_id.
    session.add(AgentApprover(team_id=team["team"], user_id=team["member"], scope="research"))
    session.commit()
    real = routes.run_and_record

    def queueing(*args: Any, **kwargs: Any) -> Any:
        row, state = real(*args, **kwargs)
        waiting = _queue(session, team)
        waiting.run_id = row.id
        session.commit()
        return row, state

    monkeypatch.setattr(routes, "run_and_record", queueing)
    client = _client(session, team["member"], chat_router=FakeRouter())

    body = client.post(
        "/api/agent/chat", json={"meeting_id": team["meeting"], "message": "x"}
    ).json()

    assert [p["tool"] for p in body["pending"]] == ["agent.share_research_document"]
    assert body["queued"] == 1


def _chat_queueing(
    session: Session,
    team: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    scope: str = "research",
    status: str = "pending",
    same_run: bool = True,
) -> dict[str, Any]:
    """One chat turn whose run leaves one proposal, shaped by the arguments."""
    real = routes.run_and_record

    def queueing(*args: Any, **kwargs: Any) -> Any:
        row, state = real(*args, **kwargs)
        left = _queue(session, team, scope=scope)
        left.run_id = row.id if same_run else None
        left.status = status
        session.commit()
        return row, state

    monkeypatch.setattr(routes, "run_and_record", queueing)
    client = _client(session, team["member"], chat_router=FakeRouter())
    body: dict[str, Any] = client.post(
        "/api/agent/chat", json={"team_id": team["team"], "message": "x"}
    ).json()
    return body


def _approver(session: Session, team: dict[str, str], scope: str) -> None:
    session.add(AgentApprover(team_id=team["team"], user_id=team["member"], scope=scope))
    session.commit()


def test_pending_hides_a_proposal_of_another_scope(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _approver(session, team, "research")

    body = _chat_queueing(session, team, monkeypatch, scope="workload")

    assert body["pending"] == []
    assert body["queued"] == 1


def test_pending_shows_any_scope_to_an_any_approver(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _approver(session, team, "any")

    body = _chat_queueing(session, team, monkeypatch, scope="workload")

    assert len(body["pending"]) == 1


def test_pending_skips_another_runs_row(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _approver(session, team, "research")

    body = _chat_queueing(session, team, monkeypatch, same_run=False)

    assert body["pending"] == [] and body["queued"] == 0


def test_pending_skips_a_decided_row(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _approver(session, team, "research")

    body = _chat_queueing(session, team, monkeypatch, status="rejected")

    assert body["pending"] == [] and body["queued"] == 0


def test_the_panel_reads_the_title_of_a_meeting_it_is_on(
    member: TestClient, team: dict[str, str]
) -> None:
    # S34's header: "{meeting title} 보고 있음". The title is A's; the agent
    # feature calls /api/agent only, so the layer reads it for the panel.
    reply = member.get("/api/agent/meeting-label", params={"meeting_id": team["meeting"]})

    assert reply.status_code == 200
    assert reply.json() == {"title": "주간 회의"}


def test_a_meeting_the_caller_cannot_read_has_no_label(
    session: Session, team: dict[str, str]
) -> None:
    outsider = _client(session, team["outsider"], chat_router=FakeRouter())

    missing = outsider.get("/api/agent/meeting-label", params={"meeting_id": team["meeting"]})
    unknown = outsider.get("/api/agent/meeting-label", params={"meeting_id": "mtg_nope"})

    assert missing.status_code == unknown.status_code == 404
    assert team["meeting"] not in missing.text


class _BusyRouter(FakeRouter):
    def route(self, request: str, subagents: Any) -> str | None:
        from autune_integrations.errors import TransientIntegrationError

        raise TransientIntegrationError("agent-router returned 429")


def test_a_model_out_of_quota_reads_as_busy_not_broken(
    session: Session, team: dict[str, str]
) -> None:
    client = _client(session, team["member"], chat_router=_BusyRouter())

    reply = client.post("/api/agent/chat", json={"team_id": team["team"], "message": "x"})

    assert reply.status_code == 503
    assert reply.json()["error"]["code"] == "agent_busy"
    run = session.query(AgentRun).one()
    assert run.outcome == "failed"


def test_a_chat_reply_says_what_did_not_go_through(
    session: Session, team: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#862: the subagent answered before L1 ran, so the reply carries the rest."""
    real = routes.run_and_record

    def refusing(*args: Any, **kwargs: Any) -> Any:
        row, state = real(*args, **kwargs)
        state["unfinished"] = [
            {"title": "리포트 초안 다시 만들기", "reason": "초안이 바뀌었습니다"}
        ]
        return row, state

    monkeypatch.setattr(routes, "run_and_record", refusing)
    client = _client(session, team["member"], chat_router=FakeRouter())

    body = client.post("/api/agent/chat", json={"team_id": team["team"], "message": "x"}).json()

    assert body["unfinished"] == [
        {"title": "리포트 초안 다시 만들기", "reason": "초안이 바뀌었습니다"}
    ]
