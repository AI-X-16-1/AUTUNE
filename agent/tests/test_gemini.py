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


def test_the_route_instructions_send_a_plain_lookup_to_no_subagent() -> None:
    sent: list[dict[str, Any]] = []

    _router('{"subagent": null}', sent).route("이 회의에서 정한 거 뭐야?", SUBAGENTS)

    instruction = sent[0]["body"]["systemInstruction"]["parts"][0]["text"]
    assert "only asks to look something up" in instruction
    assert '{"subagent": null}' in instruction


def test_the_route_instructions_send_a_lookup_to_an_assistant_that_answers_them() -> None:
    """#879: the mark ``graph.LOOKUP_MARK`` adds is the one the instructions name."""
    from autune_agent.main.graph import LOOKUP_MARK

    sent: list[dict[str, Any]] = []

    _router('{"subagent": null}', sent).route("결정 밀도가 뭐야?", SUBAGENTS)

    instruction = sent[0]["body"]["systemInstruction"]["parts"][0]["text"]
    assert LOOKUP_MARK.strip() in instruction


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


def test_compose_answers_as_the_assistant_not_about_its_findings() -> None:
    """A reply began "제공된 요약에 따르면" (2026-10-09): the model echoed the label
    it was handed. The findings carry no label to echo, and the instructions say
    to answer directly, with what was checked and the next step when empty."""
    from autune_agent.main.gemini import COMPOSE_INSTRUCTIONS

    sent: list[dict[str, Any]] = []
    outcome = SubagentResult(result=ToolResult(ok=True, summary="마감 1건."))

    _router("답변", sent).compose("마감 뭐 있어?", outcome)

    text = sent[0]["body"]["contents"][0]["parts"][0]["text"]
    assert "요약:" not in text
    assert "제공된" in COMPOSE_INSTRUCTIONS
    assert "next step" in COMPOSE_INSTRUCTIONS


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


def test_a_thought_signature_is_exempt_only_on_a_part() -> None:
    # A key the guard skips must not hide text anywhere else: model-written
    # functionCall args are echoed back, and could carry the same key name.
    sent: list[dict[str, Any]] = []
    smuggled = [
        {
            "role": "model",
            "parts": [
                {"functionCall": {"name": "a__b", "args": {"thoughtSignature": "010-1234-5678"}}}
            ],
        }
    ]

    with pytest.raises(PrivacyViolationError):
        _tools([{"text": "DONE"}], sent).step("지시", smuggled, [])
    assert sent == []


def test_a_role_is_exempt_only_on_a_turn() -> None:
    sent: list[dict[str, Any]] = []
    smuggled = [{"role": "user", "parts": [{"text": "q", "role": "010-1234-5678"}]}]

    with pytest.raises(PrivacyViolationError):
        _tools([{"text": "DONE"}], sent).step("지시", smuggled, [])
    assert sent == []


def _limited_router(sent: list[dict[str, Any]]) -> GeminiRouter:
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(429, json={"error": {"message": "quota"}})

    router = GeminiRouter(api_key="k", model="gemini-test", base_url="https://llm.test/v1beta")
    router._client._client = httpx.Client(  # noqa: SLF001 - swap the transport only
        base_url="https://llm.test/v1beta", transport=httpx.MockTransport(handle)
    )
    return router


def test_compose_falls_back_to_the_summary_when_the_model_is_out_of_quota() -> None:
    # The tools already answered; a 429 on the last call must not turn that into a 500.
    outcome = SubagentResult(result=ToolResult(ok=True, summary="열린 할 일 2건."))

    assert _limited_router([]).compose("열린 거?", outcome) == "열린 할 일 2건."


def test_compose_trims_its_findings_to_fit_the_outbound_limit() -> None:
    sent: list[dict[str, Any]] = []
    long = [{"title": f"항목 {i}", "body": "가" * 900} for i in range(5)]
    outcome = SubagentResult(result=ToolResult(ok=True, summary="다섯 건.", items=long))

    _router("답", sent).compose("정리해 줘", outcome)

    text = sent[0]["body"]["contents"][0]["parts"][0]["text"]
    instructions = sent[0]["body"]["systemInstruction"]["parts"][0]["text"]
    assert len(text) + len(instructions) <= 3800
    assert "다섯 건." in text
