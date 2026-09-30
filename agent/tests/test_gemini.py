"""The Gemini router, against a fake transport: what it sends, and what it refuses to."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from autune_agent.main.gemini import GeminiRouter
from autune_agent.results import SubagentResult, ToolResult
from autune_core.errors import PrivacyViolationError

SUBAGENTS = {"workload": "Use when asked who has too much work.", "report": "Use after a meeting."}


def _router(answer: str, sent: list[dict[str, Any]]) -> GeminiRouter:
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append({"path": request.url.path, "body": json.loads(request.content)})
        payload = {"candidates": [{"content": {"parts": [{"text": answer}]}}]}
        return httpx.Response(200, json=payload)

    router = GeminiRouter(api_key="k", model="gemini-test", base_url="https://llm.test/v1beta")
    router._client._client = httpx.Client(  # noqa: SLF001 - swap the transport only
        base_url="https://llm.test/v1beta", transport=httpx.MockTransport(handle)
    )
    return router


def test_route_returns_the_named_subagent() -> None:
    sent: list[dict[str, Any]] = []

    assert _router('{"subagent": "workload"}', sent).route("일 몰린 사람?", SUBAGENTS) == "workload"
    assert sent[0]["path"] == "/v1beta/models/gemini-test:generateContent"
    assert sent[0]["body"]["generationConfig"]["responseMimeType"] == "application/json"


@pytest.mark.parametrize("answer", ['{"subagent": "payroll"}', '{"subagent": null}', "음..."])
def test_an_unknown_null_or_unparsed_answer_is_no_route(answer: str) -> None:
    assert _router(answer, []).route("점심 메뉴", SUBAGENTS) is None


def test_no_subagents_means_no_call() -> None:
    sent: list[dict[str, Any]] = []

    assert _router("{}", sent).route("아무거나", {}) is None
    assert sent == []


def test_a_phone_number_in_the_request_is_refused_before_it_leaves() -> None:
    sent: list[dict[str, Any]] = []

    with pytest.raises(PrivacyViolationError):
        _router("{}", sent).route("010-1234-5678로 연락해 줘", SUBAGENTS)
    assert sent == []


def test_compose_sends_the_summary_and_titles_and_falls_back_to_the_summary() -> None:
    sent: list[dict[str, Any]] = []
    outcome = SubagentResult(
        result=ToolResult(ok=True, summary="마감 1건.", items=[{"title": "API 문서"}])
    )

    assert _router("", sent).compose("마감 뭐 있어?", outcome) == "마감 1건."
    text = sent[0]["body"]["contents"][0]["parts"][0]["text"]
    assert "마감 1건." in text
    assert "API 문서" in text


def test_compose_keeps_a_title_that_ends_in_a_colon() -> None:
    sent: list[dict[str, Any]] = []
    result = ToolResult(
        ok=True, summary="한 건.", items=[{"title": "다음 안건:"}], evidence=[], confidence=1.0
    )

    _router("답변", sent).compose("질문", SubagentResult(result=result))

    assert "- 다음 안건:" in json.dumps(sent[0]["body"], ensure_ascii=False)
