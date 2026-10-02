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


def test_gemini_text_sends_through_the_privacy_guard() -> None:
    from autune_agent.main.gemini import GeminiText

    sent: list[dict[str, Any]] = []
    text = GeminiText(api_key="k", model="gemini-test", base_url="https://llm.test/v1beta")

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    text._client._client = httpx.Client(  # noqa: SLF001
        base_url="https://llm.test/v1beta", transport=httpx.MockTransport(handle)
    )

    assert text.generate("지시", "본문", json_answer=False) == "ok"
    with pytest.raises(PrivacyViolationError):
        text.generate("지시", "연락처 010-1234-5678", json_answer=False)
    assert len(sent) == 1


def _tools(reply_parts: list[dict[str, Any]], sent: list[dict[str, Any]]) -> Any:
    from autune_agent.main.gemini import GeminiTools

    model = GeminiTools(api_key="k", model="gemini-test", base_url="https://llm.test/v1beta")

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": reply_parts}}]})

    model._client._client = httpx.Client(  # noqa: SLF001 - swap the transport only
        base_url="https://llm.test/v1beta", transport=httpx.MockTransport(handle)
    )
    return model


def test_tools_step_sends_declarations_and_returns_the_calls() -> None:
    from autune_agent.main.toolcall import Declaration, FunctionCall

    sent: list[dict[str, Any]] = []
    parts = [{"functionCall": {"name": "gap__open_gaps", "args": {"meeting_id": "mtg_1"}}}]
    decl = Declaration("gap__open_gaps", "Use this.", {"type": "OBJECT", "properties": {}})

    step = _tools(parts, sent).step("지시", [{"role": "user", "parts": [{"text": "갭?"}]}], [decl])

    assert step == [FunctionCall("gap__open_gaps", {"meeting_id": "mtg_1"})]
    assert sent[0]["tools"][0]["functionDeclarations"][0]["name"] == "gap__open_gaps"


def test_tools_step_returns_text_when_there_is_no_call() -> None:
    assert _tools([{"text": "DONE"}], []).step("지시", [], []) == "DONE"


def test_calls_win_over_text_in_one_reply() -> None:
    from autune_agent.main.toolcall import FunctionCall

    parts = [{"text": "먼저 볼게요"}, {"functionCall": {"name": "a__b", "args": {}}}]

    assert _tools(parts, []).step("지시", [], []) == [FunctionCall("a__b", {})]


def test_tools_step_keeps_the_parts_to_echo_back() -> None:
    parts = [{"functionCall": {"name": "a__b", "args": {}}, "thoughtSignature": "c2ln"}]
    model = _tools(parts, [])

    model.step("지시", [], [])

    assert model.last_parts == parts


def test_a_thought_signature_is_not_scanned_or_counted() -> None:
    # An opaque base64 token the model asks to get back; its digits are not
    # meeting content and must not trip the account-number pattern.
    sent: list[dict[str, Any]] = []
    turns = [
        {
            "role": "model",
            "parts": [
                {
                    "functionCall": {"name": "a__b"},
                    "thoughtSignature": "1002123456789012",
                }
            ],
        }
    ]

    _tools([{"text": "DONE"}], sent).step("지시", turns, [])

    assert len(sent) == 1


def test_tools_step_refuses_a_phone_number_before_it_leaves() -> None:
    sent: list[dict[str, Any]] = []

    with pytest.raises(PrivacyViolationError):
        _tools([{"text": "x"}], sent).step(
            "지시", [{"role": "user", "parts": [{"text": "010-1234-5678"}]}], []
        )
    assert sent == []


def test_a_reply_with_no_candidates_is_empty_text() -> None:
    from autune_agent.main.gemini import GeminiTools

    model = GeminiTools(api_key="k", model="gemini-test", base_url="https://llm.test/v1beta")
    model._client._client = httpx.Client(  # noqa: SLF001
        base_url="https://llm.test/v1beta",
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={})),
    )

    assert model.step("지시", [], []) == ""
