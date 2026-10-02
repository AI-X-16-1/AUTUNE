"""LLM client implementations. Selected by ``AUTUNE_CONTEXT_LLM_IMPL``.

- ``openai`` — Chat Completions (ChatGPT models; also any OpenAI-compatible server
  through ``AUTUNE_CONTEXT_LLM_ENDPOINT``).
- ``gemini`` — Gemini ``generateContent``.
- ``anthropic`` — the Anthropic Messages API.
- ``fake`` — scripted answers, for tests. No network.

Used only when ``engine_mode="llm"`` (see ``pipeline.llm_judge``).

The three real clients are deliberately **not** built on the providers' SDKs. This
is the one path in the module that leaves our infrastructure, and the only guard
that counts is ``autune_integrations.privacy.check_outbound`` running on every
request (invariant 11; PR #90's review). ``HttpClient.request`` runs it over the
whole body; an SDK call would go around it. Each client is a request builder and
a response reader (about thirty lines) over that one transport, so adding a
provider does not add a way out.

None of them is sent a ``temperature`` or any sampling parameter: the reasoning
models of all three providers reject non-default values.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx

from autune_context.config import LLM_DEFAULTS
from autune_context.pipeline.base import LlmUsage
from autune_integrations.base import HttpClient
from autune_integrations.errors import TransientIntegrationError

if TYPE_CHECKING:
    from collections.abc import Callable

    from autune_context.config import ContextSettings

_RETRIES = 5
"""Attempts at a rate-limited (429) or failing (5xx) request. Backoff 4s, 8s, 16s,
32s -- a minute in all, because a per-minute quota only clears after one. A newly
released model also answers 503 ("high demand") often enough that a shorter budget
aborts an evaluation run partway through."""


class LlmResponseError(Exception):
    """The API answered, but not with something a verdict can be read from.

    The message never carries the model's output or the prompt: an exception
    string reaches error tracking, which is a third party.
    """


class _HttpLlm(HttpClient):
    """One provider: how to ask (``_build``) and how to read the answer (``_read``)."""

    provider: str

    def __init__(self, settings: ContextSettings) -> None:
        key = settings.llm_api_key.get_secret_value()
        if not key:
            raise RuntimeError(f"llm_impl={self.provider} needs AUTUNE_CONTEXT_LLM_API_KEY")
        default_endpoint, default_model = LLM_DEFAULTS[self.provider]
        model = settings.llm_model or default_model
        if not model:
            raise RuntimeError(
                f"llm_impl={self.provider} has no default model: set AUTUNE_CONTEXT_LLM_MODEL"
            )
        super().__init__(
            settings.llm_endpoint or default_endpoint,
            headers={**self._auth(key), "content-type": "application/json"},
        )
        # ``HttpClient`` fixes a 10 s timeout, which is short for a model that
        # thinks before answering.
        self._client.timeout = httpx.Timeout(settings.llm_timeout_s, connect=5.0)
        self._model = model
        self._effort = settings.llm_effort
        self._usage = LlmUsage()

    @property
    def model_version(self) -> str:
        return f"{self.provider}:{self._model}"

    @property
    def usage(self) -> LlmUsage:
        return self._usage

    def complete(self, *, system: str, user: str, max_tokens: int = 512) -> str:
        path, body = self._build(system, user, max_tokens)
        started = time.monotonic()
        response = _with_retries(lambda: self.request("POST", path, json=body))
        text, input_tokens, output_tokens = self._read(response)
        self._usage.record_call(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            seconds=time.monotonic() - started,
        )
        return text

    def _auth(self, key: str) -> dict[str, str]:
        raise NotImplementedError

    def _build(self, system: str, user: str, max_tokens: int) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError

    def _read(self, response: dict[str, Any]) -> tuple[str, int, int]:
        """``(text, input tokens, output tokens)``, or ``LlmResponseError`` when
        the model declined, was cut off, or said nothing."""
        raise NotImplementedError


class OpenAiLlm(_HttpLlm):
    """``POST /v1/chat/completions``. ``max_completion_tokens`` rather than the
    older ``max_tokens``: the reasoning models reject the latter, and the others
    accept the former. No ``response_format``: not every model takes it, and the
    prompt asks for bare JSON and the reader tolerates a fence around it."""

    provider = "openai"
    service = provider  # what ``check_outbound`` and the logs name

    def _auth(self, key: str) -> dict[str, str]:
        return {"authorization": f"Bearer {key}"}

    def _build(self, system: str, user: str, max_tokens: int) -> tuple[str, dict[str, Any]]:
        body: dict[str, Any] = {
            "model": self._model,
            "max_completion_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self._effort:
            body["reasoning_effort"] = self._effort
        return "/v1/chat/completions", body

    def _read(self, response: dict[str, Any]) -> tuple[str, int, int]:
        choices = response.get("choices") or []
        if not choices:
            raise LlmResponseError("the response had no choices")
        choice = choices[0]
        message = choice.get("message") or {}
        if message.get("refusal"):
            raise LlmResponseError("the model declined the request")
        finish = choice.get("finish_reason")
        if finish == "length":
            raise LlmResponseError("the answer was cut off at max_completion_tokens")
        if finish == "content_filter":
            raise LlmResponseError("the answer was withheld by a content filter")
        text = message.get("content") or ""
        if not text:
            raise LlmResponseError("the response had no text")
        usage = response.get("usage") or {}
        return text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


class GeminiLlm(_HttpLlm):
    """``POST /v1beta/models/{model}:generateContent``. The key goes in a header,
    not the query string, so it is in neither the URL nor a log line. Thinking
    models spend ``maxOutputTokens`` on thinking too, which is why the default
    ``llm_max_tokens`` is not small. ``llm_effort`` is not sent: Gemini's thinking
    control differs between model generations, and an unknown field is a 400."""

    provider = "gemini"
    service = provider  # what ``check_outbound`` and the logs name

    def _auth(self, key: str) -> dict[str, str]:
        return {"x-goog-api-key": key}

    def _build(self, system: str, user: str, max_tokens: int) -> tuple[str, dict[str, Any]]:
        return f"/v1beta/models/{self._model}:generateContent", {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }

    def _read(self, response: dict[str, Any]) -> tuple[str, int, int]:
        if (response.get("promptFeedback") or {}).get("blockReason"):
            raise LlmResponseError("the request was blocked")
        candidates = response.get("candidates") or []
        if not candidates:
            raise LlmResponseError("the response had no candidates")
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        if finish == "MAX_TOKENS":
            raise LlmResponseError("the answer was cut off at maxOutputTokens")
        if finish not in (None, "STOP"):
            raise LlmResponseError("the model did not finish normally")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text:
            raise LlmResponseError("the response had no text")
        usage = response.get("usageMetadata") or {}
        return (
            text,
            int(usage.get("promptTokenCount", 0)),
            int(usage.get("candidatesTokenCount", 0)) + int(usage.get("thoughtsTokenCount", 0)),
        )


class AnthropicLlm(_HttpLlm):
    """``POST /v1/messages``. Thinking is left at the model's default and steered
    with ``output_config.effort`` when ``llm_effort`` is set."""

    provider = "anthropic"
    service = provider  # what ``check_outbound`` and the logs name

    def _auth(self, key: str) -> dict[str, str]:
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}

    def _build(self, system: str, user: str, max_tokens: int) -> tuple[str, dict[str, Any]]:
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if self._effort:
            body["output_config"] = {"effort": self._effort}
        return "/v1/messages", body

    def _read(self, response: dict[str, Any]) -> tuple[str, int, int]:
        stop_reason = response.get("stop_reason")
        if stop_reason == "refusal":
            raise LlmResponseError("the model declined the request")
        if stop_reason == "max_tokens":
            raise LlmResponseError("the answer was cut off at max_tokens")
        text = "".join(
            block.get("text", "")
            for block in response.get("content") or []
            if block.get("type") == "text"
        )
        if not text:
            raise LlmResponseError("the response had no text")
        usage = response.get("usage") or {}
        return text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


def _with_retries(send: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    for attempt in range(_RETRIES):
        try:
            return send()
        except TransientIntegrationError:
            if attempt == _RETRIES - 1:
                raise
            time.sleep(2 ** (attempt + 2))
    raise AssertionError("unreachable")  # pragma: no cover


class FakeLlm:
    """Answers from a script, keyed by a substring of the user prompt.

    ``FakeLlm({"금요일": '{"same_topic": true, "confidence": 0.9}'})`` answers
    that for any prompt containing "금요일". A prompt matching nothing gets
    ``default`` — an unusable reply unless the caller sets one, so a test that
    forgot a line fails loudly instead of quietly reading as "not the same".
    """

    def __init__(
        self,
        settings: ContextSettings | None = None,
        *,
        script: dict[str, str] | None = None,
        default: str | None = None,
    ) -> None:
        self._script = script or {}
        self._default = default
        self._usage = LlmUsage()
        self.prompts: list[tuple[str, str]] = []
        """Every ``(system, user)`` seen, in order — what a test asserts on."""
        self.max_tokens: list[int] = []

    @property
    def model_version(self) -> str:
        return "fake-llm-v1"

    @property
    def usage(self) -> LlmUsage:
        return self._usage

    def complete(self, *, system: str, user: str, max_tokens: int = 512) -> str:
        self.prompts.append((system, user))
        self.max_tokens.append(max_tokens)
        self._usage.record_call(input_tokens=0, output_tokens=0, seconds=0.0)
        for needle, answer in self._script.items():
            if needle in user:
                return answer
        if self._default is None:
            raise LlmResponseError("FakeLlm has no scripted answer for this prompt")
        return self._default
