"""The LLM's three judgements. No network: ``FakeLlm`` scripts the answers. The
clients that carry them out of the building are in ``test_llm_clients.py``."""

from __future__ import annotations

import json

import pytest

from autune_context.config import ContextSettings
from autune_context.pipeline.llm import FakeLlm
from autune_context.pipeline.llm_judge import LlmJudge
from autune_contracts import ChangeType


def _settings(**overrides) -> ContextSettings:
    return ContextSettings(llm_api_key="sk-test", llm_concurrency=1, **overrides)


def _judge(llm: FakeLlm, **overrides) -> LlmJudge:
    return LlmJudge(llm, _settings(**overrides))


def _topic(same: bool, confidence: float) -> str:
    return json.dumps({"same_topic": same, "confidence": confidence})


def _relation(relation: str, confidence: float) -> str:
    return json.dumps({"relation": relation, "confidence": confidence})


# --- topic relatedness ------------------------------------------------------


def test_topic_score_is_the_probability_of_the_same_topic():
    llm = FakeLlm(script={"과거A": _topic(True, 0.9), "과거B": _topic(False, 0.8)})
    scores = _judge(llm).topic_relatedness("현재", ["과거A", "과거B"])
    assert scores == pytest.approx([0.9, 0.2])


def test_an_unusable_answer_is_unjudged_and_scores_zero():
    llm = FakeLlm(script={"과거A": "죄송하지만 판단할 수 없습니다"}, default=_topic(True, 1.0))
    judge = _judge(llm)
    assert judge.topic_relatedness("현재", ["과거A", "과거B"]) == [0.0, 1.0]
    assert llm.usage.unjudged == 1


@pytest.mark.parametrize(
    "answer",
    [
        '{"same_topic": "yes", "confidence": 0.9}',  # not a boolean
        '{"same_topic": true, "confidence": "high"}',  # not a number
        '{"same_topic": true}',  # missing key
        "[1, 2]",  # not an object
        "",
    ],
)
def test_a_malformed_answer_is_unjudged_not_guessed(answer):
    llm = FakeLlm(default=answer)
    assert _judge(llm).topic_relatedness("현재", ["과거"]) == [0.0]
    assert llm.usage.unjudged == 1


def test_json_wrapped_in_prose_or_a_fence_is_read():
    llm = FakeLlm(default=f"답변입니다.\n```json\n{_topic(True, 0.7)}\n```")
    assert _judge(llm).topic_relatedness("현재", ["과거"]) == pytest.approx([0.7])


def test_confidence_outside_the_unit_interval_is_clamped():
    llm = FakeLlm(default=_topic(True, 7))
    assert _judge(llm).topic_relatedness("현재", ["과거"]) == [1.0]


# --- decision comparison -----------------------------------------------------


@pytest.mark.parametrize(
    ("relation", "change"),
    [
        ("unchanged", ChangeType.UNCHANGED),
        ("modified", ChangeType.MODIFIED),
        ("reversed", ChangeType.REVERSED),
    ],
)
def test_a_related_decision_carries_its_change_type(relation, change):
    verdict = _judge(FakeLlm(default=_relation(relation, 0.8))).compare_decisions(
        [("이전", "이후")]
    )[0]
    assert (verdict.related, verdict.change, verdict.confidence) == (True, change, 0.8)


def test_unrelated_is_not_related_and_reads_modified():
    verdict = _judge(FakeLlm(default=_relation("unrelated", 0.9))).compare_decisions(
        [("이전", "이후")]
    )[0]
    assert (verdict.related, verdict.change) == (False, ChangeType.MODIFIED)


def test_an_unknown_relation_is_unjudged():
    llm = FakeLlm(default=_relation("new", 0.9))  # NEW is never the model's to give
    verdict = _judge(llm).compare_decisions([("이전", "이후")])[0]
    assert (verdict.related, verdict.confidence) == (False, 0.0)
    assert llm.usage.unjudged == 1


def test_verdicts_stay_aligned_to_their_pairs_when_run_concurrently():
    script = {f"이후{i}": _relation("modified", i / 10) for i in range(8)}
    judge = LlmJudge(FakeLlm(script=script), ContextSettings(llm_api_key="k", llm_concurrency=4))
    verdicts = judge.compare_decisions([("이전", f"이후{i}") for i in range(8)])
    assert [v.confidence for v in verdicts] == pytest.approx([i / 10 for i in range(8)])


# --- cache -------------------------------------------------------------------


def test_the_same_pair_is_asked_once():
    llm = FakeLlm(default=_relation("unchanged", 0.9))
    judge = _judge(llm)
    judge.compare_decisions([("이전", "이후")])
    judge.compare_decisions([("이전", "이후")])
    assert llm.usage.calls == 1


def test_an_unjudged_pair_is_asked_again():
    llm = FakeLlm(default="not json")
    judge = _judge(llm)
    judge.topic_relatedness("현재", ["과거"])
    judge.topic_relatedness("현재", ["과거"])
    assert llm.usage.calls == 2


# --- what leaves the building ------------------------------------------------


def test_excerpts_are_cut_to_the_snippet_limit():
    llm = FakeLlm(default=_topic(True, 0.5))
    _judge(llm, llm_snippet_chars=10).topic_relatedness("가" * 50, ["나" * 50])
    _system, user = llm.prompts[0]
    assert user.count("가") == 10
    assert user.count("나") == 10


def test_the_configured_token_budget_is_what_the_client_is_given():
    """A reasoning model spends part of it on thinking; too small a budget cuts the
    answer off, and the setting is the operator's way to raise it."""
    llm = FakeLlm(default=_topic(True, 0.5))
    _judge(llm, llm_max_tokens=1234).topic_relatedness("현재", ["과거"])
    assert llm.max_tokens == [1234]
