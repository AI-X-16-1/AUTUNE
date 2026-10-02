"""The three provider clients, and what they let out of the building.

No network: each is pointed at an ``httpx.MockTransport``. Every test that is
about a *provider* runs for all three; a test about the shared transport runs
once.
"""

from __future__ import annotations

import json

import httpx
import pytest

from autune_context.config import ContextSettings
from autune_context.pipeline.llm import (
    AnthropicLlm,
    GeminiLlm,
    LlmResponseError,
    OpenAiLlm,
)
from autune_context.pipeline.llm_judge import _DECISION_SYSTEM, _TOPIC_SYSTEM, LlmJudge
from autune_core.errors import PrivacyViolationError
from autune_integrations.errors import PermanentIntegrationError
from autune_integrations.privacy import MAX_OUTBOUND_CHARS, check_outbound

_MODELS = {"openai": "gpt-test", "gemini": "gemini-test", "anthropic": "claude-opus-5-5"}
_CLASSES = {"openai": OpenAiLlm, "gemini": GeminiLlm, "anthropic": AnthropicLlm}
_BASE_URLS = {
    "openai": "https://api.openai.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "anthropic": "https://api.anthropic.com",
}
_PATHS = {
    "openai": "/v1/chat/completions",
    "gemini": "/v1beta/models/gemini-test:generateContent",
    "anthropic": "/v1/messages",
}


def _ok(provider: str, text: str = '{"ok": true}') -> httpx.Response:
    """A well-formed reply in the provider's own shape, 120 tokens in and 30 out."""
    if provider == "openai":
        body = {
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        }
    elif provider == "gemini":
        body = {
            "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30},
        }
    else:
        body = {
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 120, "output_tokens": 30},
        }
    return httpx.Response(200, json=body)


def _settings(provider: str, **overrides) -> ContextSettings:
    overrides.setdefault("llm_model", _MODELS[provider])
    return ContextSettings(llm_impl=provider, llm_api_key="sk-test", llm_concurrency=1, **overrides)


def _client(provider: str, handler, **overrides):
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    llm = _CLASSES[provider](_settings(provider, **overrides))
    llm._client = httpx.Client(
        base_url=_BASE_URLS[provider],
        headers=dict(llm._client.headers),
        transport=httpx.MockTransport(record),
    )
    return llm, seen


providers = pytest.mark.parametrize("provider", ["openai", "gemini", "anthropic"])


# --- the request ------------------------------------------------------------


@providers
def test_a_call_goes_to_the_providers_endpoint_and_returns_the_text(provider):
    llm, seen = _client(provider, lambda _r: _ok(provider))
    assert llm.complete(system="규칙", user="질문", max_tokens=99) == '{"ok": true}'

    (request,) = seen
    body = json.loads(request.content)
    assert request.url.path == _PATHS[provider]
    assert "sk-test" not in str(request.url)  # a key in a URL ends up in a log
    assert "규칙" in json.dumps(body, ensure_ascii=False)
    assert "질문" in json.dumps(body, ensure_ascii=False)
    assert 99 in body.get("generationConfig", body).values()
    assert not {"temperature", "top_p", "top_k"} & body.keys()
    assert not {"temperature", "topP", "topK"} & body.get("generationConfig", {}).keys()


def test_openai_sends_a_bearer_key_and_the_reasoning_field_only_when_asked():
    llm, seen = _client("openai", lambda _r: _ok("openai"))
    llm.complete(system="s", user="u")
    assert seen[0].headers["authorization"] == "Bearer sk-test"
    body = json.loads(seen[0].content)
    assert body["model"] == "gpt-test"
    assert "max_tokens" not in body  # reasoning models reject it
    assert "reasoning_effort" not in body  # a non-reasoning model rejects *that*

    llm, seen = _client("openai", lambda _r: _ok("openai"), llm_effort="low")
    llm.complete(system="s", user="u")
    assert json.loads(seen[0].content)["reasoning_effort"] == "low"


def test_gemini_puts_the_key_in_a_header_and_the_prompts_in_their_own_fields():
    llm, seen = _client("gemini", lambda _r: _ok("gemini"), llm_effort="low")
    llm.complete(system="규칙", user="질문")
    assert seen[0].headers["x-goog-api-key"] == "sk-test"
    body = json.loads(seen[0].content)
    assert body["systemInstruction"] == {"parts": [{"text": "규칙"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "질문"}]}]
    assert "thinkingConfig" not in json.dumps(body)  # effort is not sent to gemini


def test_anthropic_sends_effort_only_when_asked():
    llm, seen = _client("anthropic", lambda _r: _ok("anthropic"))
    llm.complete(system="s", user="u")
    assert seen[0].headers["x-api-key"] == "sk-test"
    assert seen[0].headers["anthropic-version"] == "2023-06-01"
    assert "output_config" not in json.loads(seen[0].content)

    llm, seen = _client("anthropic", lambda _r: _ok("anthropic"), llm_effort="low")
    llm.complete(system="s", user="u")
    assert json.loads(seen[0].content)["output_config"] == {"effort": "low"}


@providers
def test_an_endpoint_override_is_used(provider):
    llm = _CLASSES[provider](_settings(provider, llm_endpoint="https://proxy.example"))
    assert str(llm._client.base_url).startswith("https://proxy.example")


# --- the reply --------------------------------------------------------------


@providers
def test_usage_accumulates_across_calls(provider):
    llm, _ = _client(provider, lambda _r: _ok(provider))
    llm.complete(system="s", user="u1")
    llm.complete(system="s", user="u2")
    assert (llm.usage.calls, llm.usage.input_tokens, llm.usage.output_tokens) == (2, 240, 60)
    assert llm.usage.seconds >= 0


_UNUSABLE = {
    "openai": [
        {"choices": [{"message": {"refusal": "no"}, "finish_reason": "stop"}]},
        {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]},
        {"choices": [{"message": {"content": "x"}, "finish_reason": "content_filter"}]},
        {"choices": [{"message": {"content": None}, "finish_reason": "stop"}]},
        {"choices": []},
    ],
    "gemini": [
        {"promptFeedback": {"blockReason": "SAFETY"}},
        {"candidates": [{"content": {"parts": [{"text": "{"}]}, "finishReason": "MAX_TOKENS"}]},
        {"candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": "SAFETY"}]},
        {"candidates": [{"content": {"parts": [{"text": "t", "thought": True}]}}]},
        {"candidates": []},
    ],
    "anthropic": [
        {"content": [], "stop_reason": "refusal"},
        {"content": [{"type": "text", "text": "{"}], "stop_reason": "max_tokens"},
        {"content": [{"type": "thinking", "thinking": ""}]},
    ],
}


@pytest.mark.parametrize(
    ("provider", "reply"),
    [(p, r) for p, replies in _UNUSABLE.items() for r in replies],
)
def test_a_refusal_a_truncation_or_an_empty_reply_is_not_a_verdict(provider, reply):
    llm, _ = _client(provider, lambda _r: httpx.Response(200, json=reply))
    with pytest.raises(LlmResponseError):
        llm.complete(system="s", user="u")


def test_gemini_thinking_parts_are_not_the_answer_and_count_as_output():
    reply = {
        "candidates": [
            {
                "content": {"parts": [{"text": "hmm", "thought": True}, {"text": '{"a": 1}'}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
            "thoughtsTokenCount": 40,
        },
    }
    llm, _ = _client("gemini", lambda _r: httpx.Response(200, json=reply))
    assert llm.complete(system="s", user="u") == '{"a": 1}'
    assert llm.usage.output_tokens == 45


# --- the transport ----------------------------------------------------------


@providers
def test_a_rate_limited_request_is_retried(provider, monkeypatch):
    monkeypatch.setattr("autune_context.pipeline.llm.time.sleep", lambda _s: None)
    replies = iter([httpx.Response(429), httpx.Response(503), _ok(provider)])
    llm, seen = _client(provider, lambda _r: next(replies))
    assert llm.complete(system="s", user="u") == '{"ok": true}'
    assert len(seen) == 3


@providers
def test_a_rejected_request_is_not_retried(provider):
    llm, seen = _client(provider, lambda _r: httpx.Response(401))
    with pytest.raises(PermanentIntegrationError):
        llm.complete(system="s", user="u")
    assert len(seen) == 1


@providers
def test_unmasked_personal_data_never_reaches_the_transport(provider):
    llm, seen = _client(provider, lambda _r: _ok(provider))
    with pytest.raises(PrivacyViolationError):
        llm.complete(system="s", user="연락처는 010-1234-5678 입니다")
    assert seen == []


def test_the_judge_does_not_swallow_a_privacy_violation():
    """``PrivacyViolationError`` is never downgraded (autune_core.errors): an
    unusable answer is unjudged, unmasked text is a stop."""
    llm, seen = _client("openai", lambda _r: _ok("openai"))
    judge = LlmJudge(llm, _settings("openai"))
    with pytest.raises(PrivacyViolationError):
        judge.topic_relatedness("연락처는 010-1234-5678 입니다", ["과거"])
    assert seen == []


@providers
@pytest.mark.parametrize("system", [_TOPIC_SYSTEM, _DECISION_SYSTEM])
def test_the_largest_request_the_defaults_allow_passes_the_outbound_guard(provider, system):
    """Two full-size excerpts, the longest system prompt, every other string in the
    body (model id, roles, effort): still under ``MAX_OUTBOUND_CHARS`` -- so the
    guard has nothing to refuse for size, and a ``PrivacyViolationError`` can only
    mean unmasked text."""
    llm = _CLASSES[provider](_settings(provider, llm_effort="medium"))
    excerpt = "가" * _settings(provider).llm_snippet_chars
    user = f"<a>\n{excerpt}\n</a>\n<b>\n{excerpt}\n</b>"
    _path, body = llm._build(system, user, 4096)

    check_outbound(body, destination=provider)
    total = sum(len(s) for s in _strings(body))
    assert total < MAX_OUTBOUND_CHARS - 300, total  # headroom for a longer model id


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


# --- construction -----------------------------------------------------------


@providers
def test_the_api_key_is_required(provider):
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        _CLASSES[provider](ContextSettings(llm_api_key="", llm_model="m"))


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_these_providers_have_no_default_model(provider):
    """Model names turn over faster than this repository: the operator names one."""
    with pytest.raises(RuntimeError, match="LLM_MODEL"):
        _CLASSES[provider](ContextSettings(llm_api_key="sk-test"))


def test_anthropic_has_a_default_model():
    llm = AnthropicLlm(ContextSettings(llm_api_key="sk-test"))
    assert llm.model_version == "anthropic:claude-opus-5-5"


@providers
def test_the_model_version_names_the_provider_and_the_model(provider):
    llm = _CLASSES[provider](_settings(provider))
    assert llm.model_version == f"{provider}:{_MODELS[provider]}"


def test_the_api_key_is_not_in_the_settings_repr():
    assert "sk-test" not in repr(_settings("openai"))
