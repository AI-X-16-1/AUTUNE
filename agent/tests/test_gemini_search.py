"""``GeminiText.search``: the google_search tool, the question only, sources parsed."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from autune_agent.main.gemini import GeminiText, WebAnswer
from autune_core.errors import PrivacyViolationError


def _text(reply: dict[str, Any], sent: list[dict[str, Any]]) -> GeminiText:
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=reply)

    text = GeminiText(api_key="k", model="gemini-test", base_url="https://llm.test/v1beta")
    text._client._client = httpx.Client(  # noqa: SLF001 - swap the transport only
        base_url="https://llm.test/v1beta", transport=httpx.MockTransport(handle)
    )
    return text


GROUNDED = {
    "candidates": [
        {
            "content": {"parts": [{"text": "요금은 월 20달러부터입니다."}]},
            "groundingMetadata": {
                "groundingChunks": [
                    {"web": {"title": "pricing.example.com", "uri": "https://a.test/1"}},
                    {"web": {"title": "docs.example.com", "uri": "https://a.test/2"}},
                    {"web": {"title": "pricing.example.com", "uri": "https://a.test/1"}},
                    {"web": {"title": "c", "uri": "https://a.test/3"}},
                    {"web": {"title": "d", "uri": "https://a.test/4"}},
                ]
            },
        }
    ]
}


def test_search_sends_the_google_search_tool_and_the_question_only() -> None:
    sent: list[dict[str, Any]] = []

    answer = _text(GROUNDED, sent).search("Answer briefly.", "그 API 요금이 얼마지?")

    assert sent[0]["tools"] == [{"google_search": {}}]
    assert sent[0]["contents"] == [{"role": "user", "parts": [{"text": "그 API 요금이 얼마지?"}]}]
    assert "responseMimeType" not in sent[0]["generationConfig"]
    assert answer == WebAnswer(
        text="요금은 월 20달러부터입니다.",
        sources=[
            ("pricing.example.com", "https://a.test/1"),
            ("docs.example.com", "https://a.test/2"),
            ("c", "https://a.test/3"),
        ],
    )


def test_an_answer_without_grounding_has_no_sources() -> None:
    reply = {"candidates": [{"content": {"parts": [{"text": "모르겠습니다."}]}}]}

    answer = _text(reply, []).search("Answer briefly.", "질문")

    assert answer == WebAnswer(text="모르겠습니다.", sources=[])


def test_an_unmasked_question_never_leaves() -> None:
    with pytest.raises(PrivacyViolationError):
        _text(GROUNDED, []).search("Answer briefly.", "010-1234-5678 번호 주인이 누구지?")
