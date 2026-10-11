"""``extraction_llm_usage``: the token counts a provider returns with an
answer, logged a request -- and nothing else of the request or the answer.

No network: a fake provider answers with what each test gives it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from structlog.testing import capture_logs

from autune_extraction.pipeline import llm as llm_module
from autune_extraction.pipeline.base import ResolutionRequest
from autune_extraction.pipeline.llm import GeminiClient, LlmClassifier, _usage
from autune_extraction.pipeline.nli_llm import LlmNli
from autune_extraction.pipeline.resolver import LlmResolver
from autune_extraction.pipeline.summary import LlmSummarizer
from autune_integrations.errors import TransientIntegrationError

SAID = "그 갱신 버그는 제가 이번 빌드에 넣어 볼게요"
WROTE = "갱신 버그 수정은 제가 이번 빌드에 넣어 볼게요"
USAGE = {
    "promptTokenCount": 812,
    "candidatesTokenCount": 64,
    "thoughtsTokenCount": 120,
    "totalTokenCount": 996,
}
COUNTS = {"prompt_tokens": 812, "output_tokens": 64, "thinking_tokens": 120}


class Provider:
    """Answers like ``generateContent``: the text it was given, then a usage
    block when it has one. An exception in the queue is raised instead."""

    def __init__(self, *answers: Any, usage: Any = USAGE) -> None:
        self.answers = list(answers)
        self.usage = usage
        self.paths: list[str] = []

    def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        self.paths.append(path)
        answer = self.answers.pop(0) if self.answers else ""
        if isinstance(answer, Exception):
            raise answer
        response: dict[str, Any] = {"candidates": [{"content": {"parts": [{"text": answer}]}}]}
        if self.usage is not None:
            response["usageMetadata"] = self.usage
        return response


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def client(kind: type[GeminiClient], provider: Provider, *, second: str = "") -> Any:
    made = kind(api_key="k", model="first", base_url="http://llm.invalid", fallback_model=second)
    made._client = provider  # type: ignore[assignment]
    return made


def usage_events(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry["event"] == "extraction_llm_usage"]


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_module.time, "sleep", lambda _seconds: None)


def ask_summary(provider: Provider) -> None:
    provider.answers = [_dumps({"overview": "배포를 미루기로 한 회의입니다.", "points": []})]
    client(LlmSummarizer, provider).summarize([SAID])


def ask_resolver(provider: Provider) -> None:
    provider.answers = [_dumps({"summary": WROTE, "used": []})]
    request = ResolutionRequest(target=SAID, context=("네 그렇죠",), target_id="t")
    client(LlmResolver, provider).resolve_with_evidence([request])


def ask_nli(provider: Provider) -> None:
    provider.answers = [_dumps({"labels": {"1": "entailment"}})]
    client(LlmNli, provider).classify([(SAID, "화자가 이 일을 자기가 하겠다고 약속했다.")])


def ask_classifier(provider: Provider) -> None:
    provider.answers = [_dumps({"labels": {"1": "commitment"}})]
    client(LlmClassifier, provider).classify([SAID])


@pytest.mark.parametrize(
    ("ask", "step"),
    [
        (ask_classifier, "classifier"),
        (ask_nli, "nli"),
        (ask_resolver, "resolver"),
        (ask_summary, "summary"),
    ],
)
def test_each_answered_request_logs_its_counts_its_model_and_its_step(ask: Any, step: str) -> None:
    provider = Provider()

    with capture_logs() as logs:
        ask(provider)

    events = usage_events(logs)
    assert len(events) == len(provider.paths) >= 1
    for event in events:
        assert event["step"] == step and event["model"] == "first"
        assert {name: event[name] for name in COUNTS} == COUNTS
        # The counts, where they are from, and structlog's own two keys.
        assert set(event) == {"event", "log_level", "step", "model", "window", *COUNTS}


@pytest.mark.parametrize("ask", [ask_classifier, ask_nli, ask_resolver, ask_summary])
def test_nothing_that_was_said_or_written_is_in_any_log_line(ask: Any) -> None:
    with capture_logs() as logs:
        ask(Provider())

    logged = _dumps(logs)
    assert usage_events(logs)
    assert SAID not in logged and WROTE not in logged and "갱신" not in logged


@pytest.mark.parametrize("usage", [None, {}, [], "812", 812, {"promptTokenCount": None}])
def test_an_answer_without_counts_logs_nothing_and_is_still_the_answer(usage: Any) -> None:
    final = _dumps({"overview": "배포를 미루기로 한 회의입니다.", "points": []})
    provider = Provider(final, usage=usage)

    with capture_logs() as logs:
        summary = client(LlmSummarizer, provider).summarize([SAID])

    assert usage_events(logs) == []
    assert summary is not None and summary.overview == "배포를 미루기로 한 회의입니다."


@pytest.mark.parametrize(
    ("block", "counts"),
    [
        # A model that does not think returns no count for it.
        (
            {"promptTokenCount": 5, "candidatesTokenCount": 2},
            {"prompt_tokens": 5, "output_tokens": 2},
        ),
        (
            {"promptTokenCount": 0, "candidatesTokenCount": 0},
            {"prompt_tokens": 0, "output_tokens": 0},
        ),
        # Anything that is not a whole number is not a count, whatever it says.
        ({"promptTokenCount": SAID, "candidatesTokenCount": 2}, {"output_tokens": 2}),
        ({"promptTokenCount": True, "candidatesTokenCount": 2.5}, {}),
        ({"promptTokenCount": -1, "thoughtsTokenCount": [3]}, {}),
        # And a field this does not name is not logged.
        (
            {"promptTokenCount": 5, "promptTokensDetails": [{"modality": SAID}]},
            {"prompt_tokens": 5},
        ),
    ],
)
def test_only_whole_numbers_under_the_three_names_are_read(block: Any, counts: Any) -> None:
    assert _usage({"usageMetadata": block}) == counts


def test_a_usage_block_that_holds_text_never_puts_it_in_the_log() -> None:
    provider = Provider(usage={"promptTokenCount": SAID, "candidatesTokenCount": 2, SAID: 7})

    with capture_logs() as logs:
        ask_summary(provider)

    (event,) = usage_events(logs)
    assert event["output_tokens"] == 2 and "prompt_tokens" not in event
    assert SAID not in _dumps(logs)


def test_a_request_the_fallback_answered_is_logged_under_the_fallback() -> None:
    busy = [TransientIntegrationError("503") for _ in range(4)]
    final = _dumps({"overview": "배포를 미루기로 한 회의입니다.", "points": []})
    provider = Provider(*busy, final)
    summarizer = client(LlmSummarizer, provider, second="second")

    with capture_logs() as logs:
        assert summarizer.summarize([SAID]) is not None

    # Four attempts that were never answered cost nothing and log nothing.
    (event,) = usage_events(logs)
    assert event["model"] == "second" and event["step"] == "summary"
    assert len(provider.paths) == 5


def test_a_retry_that_is_answered_is_logged_once() -> None:
    final = _dumps({"overview": "배포를 미루기로 한 회의입니다.", "points": []})
    provider = Provider(TransientIntegrationError("503"), final)

    with capture_logs() as logs:
        assert client(LlmSummarizer, provider).summarize([SAID]) is not None

    (event,) = usage_events(logs)
    assert event["model"] == "first"


def test_the_last_attempt_is_logged_like_the_first() -> None:
    busy = [TransientIntegrationError("503") for _ in llm_module._RETRY_BACKOFF_SEC]
    final = _dumps({"overview": "배포를 미루기로 한 회의입니다.", "points": []})
    provider = Provider(*busy, final)

    with capture_logs() as logs:
        assert client(LlmSummarizer, provider).summarize([SAID]) is not None

    (event,) = usage_events(logs)
    assert event["model"] == "first" and event["prompt_tokens"] == 812


def test_the_resolvers_second_model_is_logged_under_its_own_name() -> None:
    # The first answer fails a check (a number nobody said), so the second
    # model is asked: two requests, two models, two lines.
    request = ResolutionRequest(target=SAID, context=("네 그렇죠",), target_id="t")
    provider = Provider(
        _dumps({"summary": "갱신 버그 수정은 제가 3월 5일 빌드에 넣어 볼게요", "used": []}),
        _dumps({"summary": WROTE, "used": []}),
    )

    with capture_logs() as logs:
        client(LlmResolver, provider, second="second").resolve_with_evidence([request])

    assert [event["model"] for event in usage_events(logs)] == ["first", "second"]
    assert {event["step"] for event in usage_events(logs)} == {"resolver"}
