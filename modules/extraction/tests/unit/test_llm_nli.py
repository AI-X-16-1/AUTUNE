"""``nli_impl=llm``: step 4's entailment question asked of a cloud model.

No network: a fake provider stands in for Gemini and records every body. The
rules under test: only the ambiguous utterances are sent, with the team's
names replaced and nothing that says who or which meeting; a meeting's worth
goes in one request, and a long one in several, each under the outbound
limit; an answer is read strictly, and anything but a clear ``entailment``
leaves the row where it was; and the implementation is switched on twice,
like every cloud one in this module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest

from autune_contracts.enums import UtteranceKind
from autune_extraction import service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.decisions import ClassifiedUtterance
from autune_extraction.pipeline import nli_llm, registry
from autune_extraction.pipeline.llm import LLM_CONFIDENCE
from autune_extraction.pipeline.nli_llm import LlmNli, batches, parse
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

HYPOTHESIS = service.COMMITMENT_HYPOTHESIS


class Provider:
    """Answers like ``generateContent`` with whatever it was told to, in order."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.bodies: list[dict[str, Any]] = []
        self.paths: list[str] = []

    def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        self.paths.append(path)
        self.bodies.append(json)
        answer = self.answers.pop(0) if self.answers else {"labels": {}}
        text = answer if isinstance(answer, str) else _dumps(answer)
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}

    def prompt(self, n: int) -> str:
        return str(self.bodies[n]["contents"][0]["parts"][0]["text"])

    @property
    def sent(self) -> str:
        return _dumps(self.bodies)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def nli(provider: Provider, **kwargs: Any) -> LlmNli:
    model = LlmNli(
        api_key="never-in-a-body", model="first", base_url="http://llm.invalid", **kwargs
    )
    model._client = provider  # type: ignore[assignment]
    return model


PREMISES = [
    "네 그건 박재경 님이랑 제가 금요일까지 올릴게요",
    "아 네네 좋네요",
    "그건 저희 팀 일이 아니라서 못 합니다",
]


def pairs(premises: list[str]) -> list[tuple[str, str]]:
    return [(premise, HYPOTHESIS) for premise in premises]


# --- what is sent ------------------------------------------------------------------


def test_a_meetings_ambiguous_lines_go_in_one_request_with_names_replaced() -> None:
    provider = Provider({"labels": {"1": "entailment", "2": "neutral", "3": "contradiction"}})
    model = nli(provider)
    model.use_roster(["박재경"])

    scores = model.classify(pairs(PREMISES))

    assert [s.label for s in scores] == ["entailment", "neutral", "contradiction"]
    assert len(provider.bodies) == 1, "free tier: twenty requests a day"
    assert provider.paths == ["/models/first:generateContent"]
    prompt = provider.prompt(0)
    assert "1. 네 그건 [사람1] 님이랑 제가 금요일까지 올릴게요" in prompt
    assert "3. 그건 저희 팀 일이 아니라서 못 합니다" in prompt
    assert f"가설: {HYPOTHESIS}" in prompt
    assert "박재경" not in provider.sent and "재경" not in provider.sent
    assert "never-in-a-body" not in provider.sent
    assert provider.bodies[0]["generationConfig"]["temperature"] == 0


def test_nothing_to_ask_sends_nothing() -> None:
    provider = Provider()

    assert nli(provider).classify([]) == []
    assert provider.bodies == []


def test_many_long_lines_go_in_several_requests_each_inside_the_outbound_limit() -> None:
    premises = [f"{n}번째 안건은 제가 한번 검토는 해 보겠습니다 " * 6 for n in range(60)]
    budget = MAX_OUTBOUND_CHARS - nli_llm._OVERHEAD - len(HYPOTHESIS)
    runs = batches(premises, budget)
    # Each request answers "entailment" for its own first line only.
    provider = Provider(*([{"labels": {"1": "entailment"}}] * len(runs)))

    scores = nli(provider).classify(pairs(premises))

    assert len(runs) > 1 and len(provider.bodies) == len(runs)
    for body in provider.bodies:
        assert len(_dumps(body)) <= MAX_OUTBOUND_CHARS
    firsts = {run[0] for run in runs}
    assert {i for i, s in enumerate(scores) if s.label == "entailment"} == firsts, (
        "a number is read against the request it was asked in"
    )
    assert sum(len(run) for run in runs) == len(premises)


def test_a_line_too_long_for_a_request_is_not_sent_and_stays_neutral() -> None:
    too_long = "가" * MAX_OUTBOUND_CHARS
    provider = Provider({"labels": {"1": "entailment", "2": "entailment"}})

    scores = nli(provider).classify(pairs([PREMISES[0], too_long, PREMISES[1]]))

    assert [s.label for s in scores] == ["entailment", "neutral", "entailment"]
    assert "가" * 50 not in provider.sent
    assert len(_dumps(provider.bodies[0])) <= MAX_OUTBOUND_CHARS


def test_no_example_in_the_prompt_names_a_placeholder() -> None:
    """A ``[사람1]`` in an example could be copied into an answer as if said."""
    assert re.search(r"\[사람\d", nli_llm._PROMPT) is None


# --- how an answer is read ------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param({"labels": {}}, id="says-nothing"),
        pytest.param({"labels": {"1": "yes"}}, id="not-a-label"),
        pytest.param({"labels": {"7": "entailment"}}, id="a-number-not-asked"),
        pytest.param({"labels": {"0": "entailment"}}, id="number-zero"),
        pytest.param({"labels": ["entailment"]}, id="not-a-mapping"),
        pytest.param("죄송하지만 판단하기 어렵습니다.", id="prose"),
        pytest.param('{"labels": {"1": "entail', id="cut-off"),
    ],
)
def test_anything_but_a_clear_answer_leaves_the_line_neutral(answer: Any) -> None:
    """Step 4 only promotes. An answer that cannot be read must cost a
    confirmation DM, never a commitment nobody made."""
    provider = Provider(answer)

    (score,) = nli(provider).classify(pairs([PREMISES[1]]))

    assert score.label == "neutral"


def test_an_answer_is_read_through_the_prose_around_it_and_whatever_its_case() -> None:
    provider = Provider('판정 결과입니다: {"labels": {"1": " Entailment "}} 이상입니다.')

    (score,) = nli(provider).classify(pairs([PREMISES[0]]))

    assert score.label == "entailment"


def test_parse_keeps_only_the_numbers_that_were_asked() -> None:
    answer = _dumps({"labels": {"1": "neutral", "2": "entailment", "3": "entailment"}})

    assert parse(answer, 2) == {1: "neutral", 2: "entailment"}


def test_the_label_is_the_largest_score_and_the_three_sum_to_one() -> None:
    provider = Provider({"labels": {"1": "entailment", "2": "contradiction"}})

    entailed, contradicted, unanswered = nli(provider).classify(pairs(PREMISES))

    assert entailed.entailment == LLM_CONFIDENCE
    assert contradicted.contradiction == LLM_CONFIDENCE
    assert unanswered.neutral == LLM_CONFIDENCE
    for score in (entailed, contradicted, unanswered):
        assert score.entailment + score.contradiction + score.neutral == pytest.approx(1.0)


def test_the_model_version_names_the_model_and_a_fallback_only_when_set() -> None:
    assert nli(Provider()).model_version == "llm:first"
    assert nli(Provider(), fallback_model="second").model_version == "llm:first+second"


# --- in step 4 ------------------------------------------------------------------------


def utterance(uid: str, kind: UtteranceKind | None, text: str) -> ClassifiedUtterance:
    return ClassifiedUtterance(id=uid, kind=kind, confidence=0.9, text=text)


def test_step_four_asks_about_the_ambiguous_lines_only_and_only_promotes() -> None:
    classified = [
        utterance("utt_1", UtteranceKind.COMMITMENT, "제가 화요일까지 배포하겠습니다"),
        utterance("utt_2", UtteranceKind.AMBIGUOUS, "네 그럼 그건 제가 챙길게요"),
        utterance("utt_3", UtteranceKind.DECISION, "배포는 금요일로 미룹니다"),
        utterance("utt_4", UtteranceKind.AMBIGUOUS, "음 한번 생각해 볼게요"),
        utterance("utt_5", None, ""),  # a non-consenting speaker's turn: no kind, no text
    ]
    provider = Provider({"labels": {"1": "entailment", "2": "contradiction"}})

    verified = service.verify_utterances(nli(provider), classified)

    by_id = {u.id: u for u in verified}
    assert by_id["utt_2"].kind is UtteranceKind.COMMITMENT and by_id["utt_2"].nli_verified
    assert by_id["utt_2"].confidence == LLM_CONFIDENCE
    assert by_id["utt_4"].kind is UtteranceKind.AMBIGUOUS, "a contradiction demotes nothing"
    assert by_id["utt_4"].nli_verified
    assert by_id["utt_1"] == classified[0] and by_id["utt_3"] == classified[2]
    assert "화요일까지 배포" not in provider.sent and "금요일로 미룹니다" not in provider.sent
    assert "챙길게요" in provider.sent and "생각해 볼게요" in provider.sent


def test_a_meeting_with_nothing_ambiguous_asks_the_provider_nothing() -> None:
    provider = Provider()
    classified = [utterance("utt_1", UtteranceKind.COMMITMENT, "제가 하겠습니다")]

    assert service.verify_utterances(nli(provider), classified) == classified
    assert provider.bodies == []


# --- switched on twice ----------------------------------------------------------------


def test_nli_impl_llm_needs_the_392_acknowledgement() -> None:
    with pytest.raises(ValueError, match="NLI_IMPL=llm"):
        ExtractionSettings(_env_file=None, nli_impl="llm")  # type: ignore[call-arg]

    ExtractionSettings(  # type: ignore[call-arg]
        _env_file=None, nli_impl="llm", llm_acknowledged_392=True
    )


def test_the_default_is_still_the_local_model_and_no_fallback() -> None:
    settings = ExtractionSettings(_env_file=None)  # type: ignore[call-arg]

    assert settings.nli_impl == "local"
    assert settings.nli_model == "gemini-3.8-flash"
    assert settings.nli_fallback_model == ""


@pytest.fixture
def registry_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """``registry.get_nli`` on settings a test fills in, its cache emptied
    before and after."""
    values: dict[str, Any] = {"nli_impl": "llm", "llm_acknowledged_392": True}
    monkeypatch.setattr(
        registry,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None, **values),  # type: ignore[call-arg]
    )
    registry.get_nli.cache_clear()
    yield values
    registry.get_nli.cache_clear()


def test_the_registry_refuses_llm_without_a_key(registry_settings: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        registry.get_nli()


def test_the_registry_builds_it_from_the_nli_models_not_the_classifiers(
    registry_settings: dict[str, Any],
) -> None:
    registry_settings.update(llm_api_key="a-key", llm_model="classifier-model")

    built = registry.get_nli()

    assert isinstance(built, LlmNli)
    assert built.model_version == "llm:gemini-3.8-flash", "its own model, and no fallback"


def test_the_task_hands_the_teams_names_to_step_four(monkeypatch: pytest.MonkeyPatch) -> None:
    """Module A masks no names; an implementation that sends text out replaces
    the roster itself, and can only replace what the task gave it (#411)."""
    seen: dict[str, Any] = {}

    class Recording:
        model_version = "recording"

        def use_roster(self, names: Any) -> None:
            seen["roster"] = list(names)

        def classify(self, asked: list[tuple[str, str]]) -> list[Any]:
            seen["asked_after_roster"] = "roster" in seen
            raise _StopError

    class _StopError(Exception):
        pass

    ambiguous = [utterance("utt_1", UtteranceKind.AMBIGUOUS, "네 제가 볼게요")]
    monkeypatch.setattr(tasks, "session_scope", _no_session)
    monkeypatch.setattr(tasks.service, "consented_utterance_ids", lambda *_: {"utt_1"})
    monkeypatch.setattr(tasks.service, "team_roster", lambda *_: ["박재경", "김민경"])
    monkeypatch.setattr(tasks.service, "decision_day", lambda *_: None)
    monkeypatch.setattr(tasks.service, "confirmed_commitment_ids", lambda *_: set())
    monkeypatch.setattr(tasks.service, "classify_utterances", lambda *_a, **_k: ambiguous)
    monkeypatch.setattr(tasks, "get_classifier", lambda: object())
    monkeypatch.setattr(tasks, "get_nli", Recording)

    with pytest.raises(_StopError):
        tasks._extract("mtg_1", [])

    assert seen == {"roster": ["박재경", "김민경"], "asked_after_roster": True}


class _NoSession:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *exc: object) -> None:
        return None


def _no_session() -> _NoSession:
    return _NoSession()
