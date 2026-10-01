"""``engine_mode``: the toggle between the trained stack and the LLM, and the two
service functions that read it. No database, no network."""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from autune_context import pipeline
from autune_context.config import ContextSettings, get_settings
from autune_context.models import CtxTopicLink
from autune_context.pipeline import registry, reset_cache
from autune_context.pipeline.llm import FakeLlm
from autune_context.pipeline.llm_judge import LlmJudge
from autune_context.pipeline.retrieval import Candidate
from autune_context.pipeline.topics import TopicSegment
from autune_context.service import (
    _assign_decisions_by_judge,
    _assign_decisions_to_threads,
    _link_topic_llm,
    _ThreadHead,
)


@pytest.fixture(autouse=True)
def _clean_caches():
    get_settings.cache_clear()
    reset_cache()
    yield
    get_settings.cache_clear()
    reset_cache()


def _settings(**overrides) -> ContextSettings:
    return ContextSettings(llm_api_key="sk-test", llm_concurrency=1, **overrides)


def _topic(same: bool, confidence: float) -> str:
    return json.dumps({"same_topic": same, "confidence": confidence})


def _relation(relation: str, confidence: float) -> str:
    return json.dumps({"relation": relation, "confidence": confidence})


# --- the toggle -------------------------------------------------------------


def test_classic_is_the_default_so_nothing_changes_until_someone_opts_in():
    assert ContextSettings().engine_mode == "classic"


def test_the_mode_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("AUTUNE_CONTEXT_ENGINE_MODE", "llm")
    get_settings.cache_clear()
    assert get_settings().engine_mode == "llm"


def test_an_unknown_mode_is_refused_at_load_not_silently_treated_as_classic():
    with pytest.raises(ValidationError):
        ContextSettings(engine_mode="ensemble")


def test_the_llm_is_built_from_the_registry_and_cached(monkeypatch):
    monkeypatch.setenv("AUTUNE_CONTEXT_LLM_IMPL", "fake")
    get_settings.cache_clear()
    assert registry.get_llm() is registry.get_llm()
    assert registry.get_llm_judge().model_version == "judge-v1+fake-llm-v1"


def test_llm_mode_without_a_chosen_provider_says_which_to_choose(monkeypatch):
    monkeypatch.delenv("AUTUNE_CONTEXT_LLM_IMPL", raising=False)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match=r"LLM_IMPL.*gemini.*openai"):
        registry.get_llm()


def test_an_unknown_llm_impl_is_rejected(monkeypatch):
    monkeypatch.setenv("AUTUNE_CONTEXT_LLM_IMPL", "does_not_exist")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="LLM_IMPL"):
        registry.get_llm()


def test_classic_mode_never_asks_for_an_llm(monkeypatch):
    """No key configured, ``llm_impl`` left at ``anthropic``: constructing the
    client would raise, so nothing in ``classic`` mode may construct it."""
    monkeypatch.setenv("AUTUNE_CONTEXT_EMBEDDER_IMPL", "fake")
    monkeypatch.setenv("AUTUNE_CONTEXT_RERANKER_IMPL", "fake")
    monkeypatch.setenv("AUTUNE_CONTEXT_NLI_IMPL", "fake")
    get_settings.cache_clear()
    registry.get_embedder()
    registry.get_reranker()
    registry.get_nli()


def test_warm_up_skips_the_reranker_and_nli_in_llm_mode(monkeypatch):
    class _Model:
        model_version = "fake"

    calls: list[str] = []

    def _tracked(name):
        def _get():
            calls.append(name)
            return _Model()

        return _get

    monkeypatch.setattr(pipeline, "get_embedder", _tracked("embedder"))
    monkeypatch.setattr(pipeline, "get_reranker", _tracked("reranker"))
    monkeypatch.setattr(pipeline, "get_nli", _tracked("nli"))
    monkeypatch.setattr(
        pipeline,
        "get_settings",
        lambda: ContextSettings(warm_models_on_worker_init=True, engine_mode="llm"),
    )
    pipeline._warm_models()
    assert calls == ["embedder"]


# --- topic linking ----------------------------------------------------------


class _Session:
    def __init__(self) -> None:
        self.rows: list[CtxTopicLink] = []

    def add(self, row: CtxTopicLink) -> None:
        self.rows.append(row)


def _candidate(meeting_id: str, passage: str, *, when: date | None = date(2026, 9, 1)) -> Candidate:
    return Candidate(
        linked_meeting_id=meeting_id,
        linked_meeting_date=when,
        topic_label=meeting_id,
        similarity=0.5,
        fusion_score=0.1,
        passage=passage,
    )


_TOPIC = TopicSegment(label="예산", text="현재 예산 논의", utterance_ids=["u1"], vector=[0.0])


def _embedder():
    return type("E", (), {"model_version": "e1"})()


def _link(candidates, llm: FakeLlm, verdicts=None, **overrides) -> list[CtxTopicLink]:
    settings = _settings(**overrides)
    session = _Session()
    _link_topic_llm(
        session,
        "mtg_now",
        _TOPIC,
        candidates,
        LlmJudge(llm, settings),
        settings,
        _embedder(),
        verdicts or {},
    )
    return session.rows


def test_a_confident_same_topic_is_asserted_a_doubtful_one_is_pending():
    llm = FakeLlm(
        script={
            "확실": _topic(True, 0.95),
            "애매": _topic(True, 0.4),  # 0.4 of same-topic: below 0.5, above the floor
            "다름": _topic(False, 0.95),  # 0.05: fairly sure it is not
        }
    )
    rows = _link(
        [_candidate("m1", "확실"), _candidate("m2", "애매"), _candidate("m3", "다름")], llm
    )

    by_meeting = {r.linked_meeting_id: r for r in rows}
    assert set(by_meeting) == {"m1", "m2"}  # m3 is under llm_pending_floor: no row at all
    assert by_meeting["m1"].status == "asserted"
    assert by_meeting["m2"].status == "pending"
    assert by_meeting["m1"].rerank_score == pytest.approx(0.95)
    assert by_meeting["m1"].reranker_version == "judge-v1+fake-llm-v1"


def test_dense_similarity_decides_nothing_in_llm_mode():
    llm = FakeLlm(default=_topic(False, 0.99))
    candidate = Candidate(
        linked_meeting_id="m1",
        linked_meeting_date=date(2026, 9, 1),
        topic_label="m1",
        similarity=0.99,  # far over link_similarity_threshold
        fusion_score=0.1,
        passage="p",
    )
    assert _link([candidate], llm) == []


def test_only_the_configured_number_of_candidates_are_asked_about():
    llm = FakeLlm(default=_topic(True, 0.9))
    rows = _link([_candidate(f"m{i}", f"p{i}") for i in range(10)], llm, llm_topic_candidates=3)
    assert llm.usage.calls == 3
    assert {r.linked_meeting_id for r in rows} == {"m0", "m1", "m2"}


def test_a_candidate_with_no_date_gets_no_link():
    llm = FakeLlm(default=_topic(True, 0.9))
    assert _link([_candidate("m1", "p", when=None)], llm) == []


def test_no_candidates_means_no_call():
    llm = FakeLlm(default=_topic(True, 0.9))
    assert _link([], llm) == []
    assert llm.usage.calls == 0


# --- thread matching --------------------------------------------------------


def _heads() -> list[_ThreadHead]:
    return [
        _ThreadHead("thr_a", [1.0, 0.0], "예산은 500만 원"),
        _ThreadHead("thr_b", [0.9, 0.1], "배포는 금요일"),
        _ThreadHead("thr_c", [0.0, 1.0], "채용은 보류"),
    ]


def test_the_llm_decides_the_thread_where_the_cosine_would_have_picked_another():
    """The decision's vector is 0.30 cosine to ``thr_a`` and 0.95 to ``thr_c``: the
    cosine path threads it onto ``thr_c``. The LLM, shown both, says it is the
    budget decision revised -- ``thr_a`` -- and that is what ``llm`` mode does."""

    class _ByEarlier(FakeLlm):
        def complete(self, *, system, user, max_tokens=512):
            super().complete(system=system, user=user, max_tokens=max_tokens)
            related = "<earlier>\n예산은 500만 원\n</earlier>" in user
            return _relation("modified" if related else "unrelated", 0.9)

    vectors = [[0.3, 0.95]]
    assert _assign_decisions_to_threads(vectors, _heads(), 0.65)[0].thread_id == "thr_c"

    assignment = _assign_decisions_by_judge(
        ["예산을 700만 원으로"],
        vectors,
        _heads(),
        LlmJudge(_ByEarlier(default=""), _settings()),
        _settings(),
    )
    assert assignment[0].thread_id == "thr_a"


def test_a_low_confidence_match_is_not_taken():
    llm = FakeLlm(default=_relation("unchanged", 0.3))
    assert (
        _assign_decisions_by_judge(
            ["x"], [[1.0, 0.0]], _heads(), LlmJudge(llm, _settings()), _settings()
        )
        == {}
    )


def test_each_decision_is_put_to_the_llm_against_its_closest_threads_only():
    llm = FakeLlm(default=_relation("unrelated", 0.9))
    _assign_decisions_by_judge(
        ["x"],
        [[1.0, 0.0]],
        _heads(),
        LlmJudge(llm, _settings()),
        _settings(llm_thread_candidates=2),
    )
    asked = {user.split("<earlier>\n")[1].split("\n</earlier>")[0] for _s, user in llm.prompts}
    assert asked == {"예산은 500만 원", "배포는 금요일"}  # thr_c is the farthest


def test_a_thread_listed_twice_is_asked_about_once():
    """A reprocessed meeting's own version sits in ``heads`` next to its team head."""
    heads = [*_heads(), _ThreadHead("thr_a", [0.99, 0.01], "예산은 400만 원")]
    llm = FakeLlm(default=_relation("unrelated", 0.9))
    _assign_decisions_by_judge(
        ["x"], [[1.0, 0.0]], heads, LlmJudge(llm, _settings()), _settings(llm_thread_candidates=4)
    )
    assert llm.usage.calls == 3  # a, b, c -- not a, a, b, c


def test_two_decisions_cannot_take_the_same_thread():
    llm = FakeLlm(default=_relation("modified", 0.9))
    assignment = _assign_decisions_by_judge(
        ["첫째", "둘째"],
        [[1.0, 0.0], [1.0, 0.0]],
        [_ThreadHead("thr_a", [1.0, 0.0], "예산")],
        LlmJudge(llm, _settings()),
        _settings(),
    )
    assert len(assignment) == 1


# --- hybrid: classic asserts, the LLM checks -------------------------------


class _Reranker:
    model_version = "rr1"

    def score(self, _query, passages):
        return [0.0] * len(passages)  # only dense similarity decides in these tests


def _hybrid_candidate(meeting_id: str, passage: str, *, similarity: float, when=date(2026, 9, 1)):
    return Candidate(
        linked_meeting_id=meeting_id,
        linked_meeting_date=when,
        topic_label=meeting_id,
        similarity=similarity,
        fusion_score=0.1,
        passage=passage,
    )


def _hybrid(candidates, llm: FakeLlm, verdicts=None, **overrides) -> list[CtxTopicLink]:
    from autune_context.service import _link_topic

    settings = _settings(**overrides)
    session = _Session()
    _link_topic(
        session,
        "mtg_now",
        _TOPIC,
        candidates,
        _Reranker(),
        settings,
        _embedder(),
        verdicts or {},
        verifier=LlmJudge(llm, settings),
    )
    return session.rows


def test_a_link_the_llm_confirms_stays_asserted_and_carries_its_confidence():
    llm = FakeLlm(default=_topic(True, 0.93))
    (row,) = _hybrid([_hybrid_candidate("m1", "p", similarity=0.9)], llm)
    assert row.status == "asserted"
    assert row.confidence == pytest.approx(0.93)
    assert row.rerank_score == 0.0  # still the re-ranker's number
    assert row.reranker_version == "rr1|judge-v1+fake-llm-v1"


def test_a_link_the_llm_is_sure_is_wrong_is_dropped():
    llm = FakeLlm(default=_topic(False, 0.95))
    assert _hybrid([_hybrid_candidate("m1", "p", similarity=0.9)], llm) == []


def test_a_link_the_llm_doubts_is_demoted_to_pending():
    llm = FakeLlm(default=_topic(True, 0.4))  # under llm_link_threshold, over the floor
    (row,) = _hybrid([_hybrid_candidate("m1", "p", similarity=0.9)], llm)
    assert row.status == "pending"


def test_only_links_classic_would_assert_are_put_to_the_llm():
    llm = FakeLlm(default=_topic(True, 0.9))
    rows = _hybrid(
        [
            _hybrid_candidate("strong", "strong", similarity=0.9),
            _hybrid_candidate("weak", "weak", similarity=0.5),  # classic: pending
        ],
        llm,
    )
    assert llm.usage.calls == 1
    by_meeting = {r.linked_meeting_id: r for r in rows}
    assert by_meeting["strong"].status == "asserted"
    assert by_meeting["weak"].status == "pending"
    assert by_meeting["weak"].reranker_version == "rr1"  # never judged, says so


def test_an_answer_that_cannot_be_read_is_not_a_veto():
    llm = FakeLlm(default="I cannot help with that")
    (row,) = _hybrid([_hybrid_candidate("m1", "p", similarity=0.9)], llm)
    assert row.status == "asserted"  # classic's verdict stands
    assert row.reranker_version == "rr1|judge-v1+fake-llm-v1"
    assert llm.usage.unjudged == 1


def test_a_candidate_with_no_date_is_not_asked_about():
    llm = FakeLlm(default=_topic(True, 0.9))
    assert _hybrid([_hybrid_candidate("m1", "p", similarity=0.9, when=None)], llm) == []
    assert llm.usage.calls == 0


def test_without_a_verifier_topic_linking_is_classic():
    from autune_context.service import _link_topic

    settings = _settings()
    session = _Session()
    _link_topic(
        session,
        "mtg_now",
        _TOPIC,
        [_hybrid_candidate("m1", "p", similarity=0.9)],
        _Reranker(),
        settings,
        _embedder(),
        {},
    )
    (row,) = session.rows
    assert (row.status, row.reranker_version) == ("asserted", "rr1")
    assert row.confidence == pytest.approx(0.9)


def test_verify_topics_says_none_for_an_unjudged_passage_where_relatedness_says_zero():
    llm = FakeLlm(script={"좋은": _topic(True, 0.8)}, default="not json")
    judge = LlmJudge(llm, _settings())
    assert judge.verify_topics("현재", ["좋은", "나쁜"]) == [pytest.approx(0.8), None]
    assert judge.topic_relatedness("현재", ["좋은", "나쁜"]) == [pytest.approx(0.8), 0.0]


def test_warm_up_covers_the_reranker_and_nli_in_hybrid_mode(monkeypatch):
    class _Model:
        model_version = "fake"

    calls: list[str] = []

    def _tracked(name):
        def _get():
            calls.append(name)
            return _Model()

        return _get

    monkeypatch.setattr(pipeline, "get_embedder", _tracked("embedder"))
    monkeypatch.setattr(pipeline, "get_reranker", _tracked("reranker"))
    monkeypatch.setattr(pipeline, "get_nli", _tracked("nli"))
    monkeypatch.setattr(
        pipeline,
        "get_settings",
        lambda: ContextSettings(warm_models_on_worker_init=True, engine_mode="hybrid"),
    )
    pipeline._warm_models()
    assert set(calls) == {"embedder", "reranker", "nli"}


def test_the_comparison_reads_every_mode_against_the_first():
    from autune_context.eval._mode import comparison

    text = comparison(
        "topic-linking",
        {
            "classic": ({"accuracy": 0.90, "precision": 0.80}, {"a": True, "b": False, "c": True}),
            "llm": ({"accuracy": 0.95, "precision": 1.00}, {"a": True, "b": True, "c": False}),
            "hybrid": ({"accuracy": 1.00, "precision": 1.00}, {"a": True, "b": True, "c": True}),
        },
    )
    assert "classic vs llm vs hybrid" in text
    assert "0.900" in text and "0.950" in text and "1.000" in text
    assert "llm vs classic: right where classic was wrong (1): b" in text
    assert "llm vs classic: wrong where classic was right (1): c" in text
    assert "hybrid vs classic: wrong where classic was right (0): -" in text


# --- a user's answer to a link is theirs, in every mode ----------------------


def test_a_link_the_user_answered_keeps_the_answer_in_llm_mode_and_is_not_asked():
    llm = FakeLlm(default=_topic(False, 0.95))  # the model would drop it
    rows = _link(
        [_candidate("m1", "p1"), _candidate("m2", "p2")],
        llm,
        verdicts={("m1", "예산"): "confirmed"},
    )
    by_meeting = {r.linked_meeting_id: r for r in rows}
    assert by_meeting["m1"].status == "confirmed"
    assert "m2" not in by_meeting  # the model's own call still applies to the rest
    assert llm.usage.calls == 1  # only m2 was put to it


def test_a_link_the_user_answered_keeps_the_answer_in_hybrid_mode_and_is_not_asked():
    llm = FakeLlm(default=_topic(False, 0.95))  # would veto both
    rows = _hybrid(
        [
            _hybrid_candidate("m1", "p1", similarity=0.9),
            _hybrid_candidate("m2", "p2", similarity=0.9),
        ],
        llm,
        verdicts={("m1", "예산"): "rejected"},
    )
    assert {r.linked_meeting_id: r.status for r in rows} == {"m1": "rejected"}
    assert llm.usage.calls == 1
