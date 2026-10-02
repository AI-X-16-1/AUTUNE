"""``resolver_impl=llm``: what it sends, what it keeps, and when it gives the quote back.

No network: a fake provider stands in for Gemini and records every body. The one
rule that decides everything here is the resolver's own -- one bad answer never
costs the meeting its item, it costs that item its rewrite.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from autune_core.errors import PrivacyViolationError
from autune_extraction.config import ExtractionSettings
from autune_extraction.pipeline import registry
from autune_extraction.pipeline.base import ResolutionRequest, give_roster
from autune_extraction.pipeline.resolver import MAX_ESCALATIONS, LlmResolver
from autune_integrations.errors import TransientIntegrationError

ROSTER = ["박 재경", "김민경"]


class Provider:
    """Answers like ``generateContent`` with whatever it was told to."""

    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.bodies: list[dict[str, Any]] = []
        self.paths: list[str] = []

    def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        self.bodies.append(json)
        self.paths.append(path)
        answer = self.answers.pop(0) if self.answers else ""
        if isinstance(answer, Exception):
            raise answer
        return {"candidates": [{"content": {"parts": [{"text": answer}]}}]}

    @property
    def sent(self) -> str:
        return json.dumps(self.bodies, ensure_ascii=False)


def resolver(provider: Provider) -> LlmResolver:
    r = LlmResolver(api_key="never-in-a-body", model="gemini-test", base_url="http://llm.invalid")
    r._client = provider  # type: ignore[assignment]
    return r


TARGET = "그럼 제가 다음 주 화요일까지 볼게요"
CONTEXT = ("지난주 고객 인터뷰 결과가 아직 정리가 안 됐어요",)


def request(**kwargs: Any) -> ResolutionRequest:
    return ResolutionRequest(target=kwargs.pop("target", TARGET), context=CONTEXT, **kwargs)


def test_a_grounded_answer_is_the_resolved_sentence() -> None:
    provider = Provider("그럼 제가 다음 주 화요일까지 고객 인터뷰 결과를 볼게요")

    (out,) = resolver(provider).resolve([request()])

    assert out == "그럼 제가 다음 주 화요일까지 고객 인터뷰 결과를 볼게요"
    assert provider.bodies[0]["generationConfig"]["temperature"] == 0


def test_the_request_carries_the_target_and_its_context_and_nothing_else() -> None:
    provider = Provider(TARGET)

    resolver(provider).resolve([request(context_after=("네 알겠습니다",))])

    sent = provider.sent
    assert TARGET in sent and CONTEXT[0] in sent and "알겠습니다" in sent
    assert "never-in-a-body" not in sent, "the key travels in a header, never a body"


def test_no_roster_name_reaches_the_provider_and_the_answer_gets_it_back() -> None:
    provider = Provider("재경 님이 정리한 고객 인터뷰 결과를 제가 볼게요")
    r = resolver(provider)
    r.use_roster(ROSTER)

    (out,) = r.resolve(
        [
            ResolutionRequest(
                target="재경 님이 정리한 그거 제가 볼게요",
                context=("박재경 님이 고객 인터뷰 결과를 정리했어요",),
            )
        ]
    )

    for name in ("박재경", "재경", "박 재경"):
        assert name not in provider.sent
    assert "[사람1]" in provider.sent
    # The team reads this description: the name is back, not a placeholder.
    assert "[사람" not in out
    assert out == "재경 님이 정리한 고객 인터뷰 결과를 제가 볼게요"


def test_a_placeholder_the_model_invented_gives_the_quote_back() -> None:
    r = resolver(Provider("[사람2] 님이 정리한 것을 볼게요"))
    r.use_roster(ROSTER)

    (out,) = r.resolve([request(target="재경 님이 정리한 그거 볼게요")])

    assert out == "재경 님이 정리한 그거 볼게요"


@pytest.mark.parametrize(
    "answer",
    [
        "",  # blank, or a blocked answer
        "고객 인터뷰 결과를 볼게요\n그리고 다른 문장도요",  # more than one sentence
        "그럼 제가 다음 주 화요일까지 3건을 볼게요",  # a number that was never said
    ],
)
def test_an_unusable_answer_gives_the_quote_back(answer: str) -> None:
    (out,) = resolver(Provider(answer)).resolve([request()])

    assert out == TARGET


def test_a_sentence_that_loses_the_verb_ending_gives_the_quote_back() -> None:
    (out,) = resolver(Provider("고객 인터뷰 결과 검토")).resolve([request()])

    assert out == TARGET


def test_quotes_around_the_answer_are_dropped() -> None:
    provider = Provider('"그럼 제가 다음 주 화요일까지 고객 인터뷰 결과를 볼게요"')

    (out,) = resolver(provider).resolve([request()])

    assert out == "그럼 제가 다음 주 화요일까지 고객 인터뷰 결과를 볼게요"


def test_a_failed_call_costs_that_item_its_rewrite_not_the_meeting() -> None:
    provider = Provider(RuntimeError("gateway page"), "그럼 제가 다음 주 화요일까지 자료를 볼게요")

    first, second = resolver(provider).resolve([request(), request()])

    assert first == TARGET
    assert second == "그럼 제가 다음 주 화요일까지 자료를 볼게요"


def test_a_privacy_refusal_is_raised_never_downgraded() -> None:
    with pytest.raises(PrivacyViolationError):
        resolver(Provider(PrivacyViolationError("unmasked number"))).resolve([request()])


def test_a_busy_model_falls_back_to_the_second_one(monkeypatch: pytest.MonkeyPatch) -> None:
    from autune_extraction.pipeline import llm as llm_module

    monkeypatch.setattr(llm_module.time, "sleep", lambda _s: None)
    provider = Provider(
        TransientIntegrationError("503"),
        TransientIntegrationError("503"),
        TransientIntegrationError("503"),
        "그럼 제가 다음 주 화요일까지 자료를 볼게요",
    )
    r = LlmResolver(api_key="k", model="big", base_url="http://llm.invalid", fallback_model="small")
    r._client = provider  # type: ignore[assignment]

    (out,) = r.resolve([request()])

    assert out == "그럼 제가 다음 주 화요일까지 자료를 볼게요"
    assert r.model_version == "llm:big+small"


def test_the_task_hands_the_roster_to_a_resolver_that_sends_text_out() -> None:
    r = resolver(Provider())
    give_roster(r, ROSTER)
    assert r._roster == tuple(ROSTER)


# --- the registry -----------------------------------------------------------------


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch):
    def apply(**overrides: Any) -> None:
        monkeypatch.setattr(
            registry,
            "get_settings",
            lambda: ExtractionSettings(_env_file=None, **overrides),  # type: ignore[call-arg]
        )

    registry.get_resolver.cache_clear()
    yield apply
    registry.get_resolver.cache_clear()


def test_llm_without_a_key_is_refused_by_name(configured) -> None:
    configured(resolver_impl="llm", llm_api_key="", AUTUNE_LLM_API_KEY="")
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        registry.get_resolver()


def test_llm_with_a_key_builds_and_needs_no_checkpoint(configured) -> None:
    configured(resolver_impl="llm", llm_api_key="k", resolver_model="m", resolver_second_model="")

    built = registry.get_resolver()

    assert isinstance(built, LlmResolver)
    assert built.model_version == "llm:m"


# --- the second model --------------------------------------------------------------


def two_models(provider: Provider) -> LlmResolver:
    r = LlmResolver(
        api_key="k",
        model="first",
        base_url="http://llm.invalid",
        fallback_model="second",
    )
    r._client = provider  # type: ignore[assignment]
    return r


GOOD = "그럼 제가 다음 주 화요일까지 고객 인터뷰 결과를 볼게요"


def models_asked(provider: Provider) -> list[str]:
    return [p.split("/models/")[1].split(":")[0] for p in provider.paths]


@pytest.mark.parametrize(
    "bad",
    [
        "그럼 제가 [디비 작업이 없는 이번 회의는] 다음 주 화요일까지 볼게요",  # a clause of its own
        "그럼 제가 (고객 인터뷰 결과를) 다음 주 화요일까지 볼게요",
        "그럼 제가 고객 인터뷰 결과를 볼게요",  # the deadline is gone
        "그럼 제가 다음 주 화요일까지 " + "지난주 고객 인터뷰 결과를 " * 12 + "볼게요",  # runs on
    ],
)
def test_an_unsound_first_answer_goes_to_the_second_model_once(bad: str) -> None:
    provider = Provider(bad, GOOD)

    (out,) = two_models(provider).resolve([request()])

    assert out == GOOD
    assert models_asked(provider) == ["first", "second"]


def test_when_the_second_model_fails_the_checks_too_the_raw_quote_stands() -> None:
    bad = "그럼 제가 [덧붙인 절] 다음 주 화요일까지 볼게요"
    provider = Provider(bad, bad)

    (out,) = two_models(provider).resolve([request()])

    assert out == TARGET
    assert len(provider.bodies) == 2, "asked twice, never a third time"


def test_one_meeting_asks_the_second_model_at_most_max_escalations_times() -> None:
    """Its free tier allows twenty a day (#530 review): one meeting of unsound
    first answers must not spend them all."""
    bad = "그럼 제가 [덧붙인 절] 다음 주 화요일까지 볼게요"
    n = MAX_ESCALATIONS + 3
    provider = Provider(*([bad] * (2 * n)))

    out = two_models(provider).resolve([request() for _ in range(n)])

    assert out == [TARGET] * n
    assert models_asked(provider).count("second") == MAX_ESCALATIONS


def test_a_sound_first_answer_never_reaches_the_second_model() -> None:
    provider = Provider(GOOD)

    two_models(provider).resolve([request()])

    assert models_asked(provider) == ["first"]


def test_without_a_second_model_an_unsound_answer_is_the_raw_quote() -> None:
    provider = Provider("그럼 제가 [덧붙인 절] 다음 주 화요일까지 볼게요")

    (out,) = resolver(provider).resolve([request()])

    assert out == TARGET
    assert len(provider.bodies) == 1


def test_a_masked_token_the_context_already_has_is_not_a_clause_of_its_own() -> None:
    req = ResolutionRequest(
        target="그럼 제가 다음 주 화요일까지 볼게요",
        context=("[전화번호] 쪽 문의가 아직 정리가 안 됐어요",),
    )
    provider = Provider("그럼 제가 다음 주 화요일까지 [전화번호] 쪽 문의를 볼게요")

    (out,) = two_models(provider).resolve([req])

    assert out == "그럼 제가 다음 주 화요일까지 [전화번호] 쪽 문의를 볼게요"
    assert models_asked(provider) == ["first"]


def test_a_second_model_that_already_answered_a_busy_first_is_not_asked_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autune_extraction.pipeline import llm as llm_module

    monkeypatch.setattr(llm_module.time, "sleep", lambda _s: None)
    busy = TransientIntegrationError("503")
    provider = Provider(busy, busy, busy, busy, "그럼 제가 [덧붙인 절] 다음 주 화요일까지 볼게요")

    (out,) = two_models(provider).resolve([request()])

    assert out == TARGET
    assert models_asked(provider) == ["first"] * 4 + ["second"]


def test_the_registry_gives_the_resolver_its_own_two_models(configured) -> None:
    configured(
        resolver_impl="llm",
        llm_api_key="k",
        llm_model="the-classifiers",
        llm_fallback_model="the-classifiers-second",
    )

    built = registry.get_resolver()

    assert built.model_version == "llm:gemini-3.5-flash-lite+gemini-3.8-flash"
