"""Relation assistance — ``pipeline.relation_assist`` and ``relations.hard_pairs``.

Nothing here calls a real LLM. ``FakeRelationAsker`` stands in for the model
where the question is what gets asked and what an answer may add; the Gemini
client is driven through an httpx mock transport where the question is what a
request would have carried.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from autune_core.errors import PrivacyViolationError
from autune_gap import graph
from autune_gap.config import get_settings
from autune_gap.pipeline import (
    AssistedRelations,
    Entity,
    FakeRelationAsker,
    GeminiRelationAsker,
    Relation,
    RelationExtractor,
    RuleRelations,
)
from autune_gap.pipeline.registry import get_relation_extractor, reset_cache
from autune_gap.pipeline.relation_assist import (
    INSTRUCTIONS,
    PairQuestion,
    batches,
    parse,
    render,
)
from autune_gap.pipeline.relations import hard_pairs, mention_spans, relations_in
from autune_integrations.privacy import MAX_OUTBOUND_CHARS


def pairs(text: str, *mentions: str) -> list[tuple[str, str]]:
    spans = mention_spans(text, mentions)
    return hard_pairs(text, spans, relations_in(text, spans))


def entities(*names: str) -> list[Entity]:
    return [Entity(text=name, label="term", utterance_id="u0") for name in names]


# --- what the rules decline -----------------------------------------------------------

GLUE = "실시간 개인화로 합의했는데 인기순 정렬 얘기가 나왔네요"
POSSESSIVE = "검색 기능의 정렬 로직은 다음 주에 봅니다"
PROCESSING = "캐시 처리 때문에 정렬 로직이 막혀 있습니다"


def test_contrast_glue_is_a_hard_pair() -> None:
    """``는데`` is a contrast here and glue elsewhere; the rules take neither."""
    assert pairs(GLUE, "실시간 개인화", "인기순 정렬") == [("실시간 개인화", "인기순 정렬")]


def test_a_bare_possessive_is_a_hard_pair() -> None:
    """의 is composition or possession, and the rules have no ``part_of``."""
    assert pairs(POSSESSIVE, "검색 기능", "정렬 로직") == [("검색 기능", "정렬 로직")]


def test_a_reason_read_as_resolved_is_a_hard_pair() -> None:
    """The documented price of ``_resolved``: 처리 names the work, and the rule
    drops the blocker. That is the case handed to the model."""
    assert relations_in(PROCESSING, mention_spans(PROCESSING, ["캐시", "정렬 로직"])) == []
    assert pairs(PROCESSING, "캐시", "정렬 로직") == [("캐시", "정렬 로직")]


def test_a_pair_the_rules_typed_is_not_asked_about() -> None:
    text = "인기순 정렬 대신 실시간 개인화로 가는데 캐시는 봐야죠"
    spans = mention_spans(text, ["인기순 정렬", "실시간 개인화", "캐시"])
    typed = relations_in(text, spans)

    assert ("인기순 정렬", "실시간 개인화", "alternative_to") in typed
    assert hard_pairs(text, spans, typed) == [("실시간 개인화", "캐시")]


def test_a_plain_sentence_asks_nothing() -> None:
    assert pairs("검색 기능하고 캐시 얘기를 했습니다", "검색 기능", "캐시") == []


def test_two_topics_far_apart_are_not_a_pair() -> None:
    far = "검색 기능" + "는데 " + "가" * 50 + " 캐시"
    assert pairs(far, "검색 기능", "캐시") == []


# --- what the assisted extractor keeps and adds ---------------------------------------


def assisted(asker: Any, *, max_utterances: int = 30) -> AssistedRelations:
    return AssistedRelations(RuleRelations(), asker, max_utterances=max_utterances)


def test_the_rules_stand_and_only_hard_utterances_are_asked() -> None:
    asker = FakeRelationAsker()
    utterances = [
        ("u1", "정렬 로직은 인덱스가 필요합니다"),
        ("u2", "검색 기능하고 캐시 얘기를 했습니다"),
        ("u3", POSSESSIVE),
    ]

    found = assisted(asker).extract(
        utterances, entities("정렬 로직", "인덱스", "검색 기능", "캐시")
    )

    assert [(r.source, r.target, r.relation, r.asserted_by) for r in found] == [
        ("정렬 로직", "인덱스", "depends_on", "rules-3")
    ]
    assert asker.asked == [PairQuestion(POSSESSIVE, (("검색 기능", "정렬 로직"),))]


def test_an_answer_adds_a_relation_attributed_to_the_model() -> None:
    asker = FakeRelationAsker(lambda q: frozenset({("정렬 로직", "blocked_by", "캐시")}))

    found = assisted(asker).extract([("u1", PROCESSING)], entities("캐시", "정렬 로직"))

    assert [(r.source, r.target, r.relation, r.utterance_id, r.asserted_by) for r in found] == [
        ("정렬 로직", "캐시", "blocked_by", "u1", "fake")
    ]


def test_a_symmetric_answer_is_written_both_ways() -> None:
    asker = FakeRelationAsker(
        lambda q: frozenset({("실시간 개인화", "alternative_to", "인기순 정렬")})
    )

    found = assisted(asker).extract([("u1", GLUE)], entities("실시간 개인화", "인기순 정렬"))

    assert {(r.source, r.target) for r in found} == {
        ("실시간 개인화", "인기순 정렬"),
        ("인기순 정렬", "실시간 개인화"),
    }


def test_an_unanswered_question_leaves_the_rules_alone() -> None:
    asker = FakeRelationAsker(lambda q: None)

    assert assisted(asker).extract([("u1", GLUE)], entities("실시간 개인화", "인기순 정렬")) == []


def test_no_more_than_the_cap_leaves_one_meeting() -> None:
    asker = FakeRelationAsker()
    utterances = [(f"u{i}", POSSESSIVE) for i in range(5)]

    assisted(asker, max_utterances=2).extract(utterances, entities("검색 기능", "정렬 로직"))

    assert len(asker.asked) == 2


def test_a_meeting_with_nothing_hard_makes_no_call() -> None:
    asked: list[Any] = []

    class Refusing:
        model_version = "never"

        def ask(self, questions: list[PairQuestion]) -> list[Any]:
            asked.append(questions)
            return []

    assisted(Refusing()).extract([("u1", "검색 기능을 봅니다")], entities("검색 기능"))

    assert asked == []


def test_a_rule_edge_keeps_the_rule_when_the_model_says_it_too() -> None:
    """``graph.relation_edges`` keeps the first relation of a triple, and the
    assisted extractor lists the rules first."""
    text = "정렬 로직은 인덱스가 필요합니다"
    rules = RuleRelations().extract([("u1", text)], entities("정렬 로직", "인덱스"))
    model = [Relation("정렬 로직", "인덱스", "depends_on", "u1", asserted_by="gemini:x")]
    topics = graph.build_topics(
        [Entity("정렬 로직", "term", "u1"), Entity("인덱스", "term", "u1")], ["u1"]
    )
    tagged = [
        Relation(r.source, r.target, r.relation, r.utterance_id, asserted_by="rules-3")
        for r in rules
    ]

    edges = graph.relation_edges(topics, tagged + model)

    assert [(e.relation, e.asserted_by) for e in edges] == [("depends_on", "rules-3")]


def test_the_assisted_extractor_satisfies_the_protocol() -> None:
    assert isinstance(assisted(FakeRelationAsker()), RelationExtractor)
    assert assisted(FakeRelationAsker()).model_version == "rules-3+fake"


# --- the prompt and the answer ---------------------------------------------------------

QUESTION = PairQuestion(PROCESSING, (("캐시", "정렬 로직"),))
OTHER = PairQuestion(GLUE, (("실시간 개인화", "인기순 정렬"),))


def test_the_prompt_letters_mentions_per_line() -> None:
    text, lettered = render([QUESTION, OTHER])

    assert lettered == {
        1: {"A": "캐시", "B": "정렬 로직"},
        2: {"A": "실시간 개인화", "B": "인기순 정렬"},
    }
    assert f"[발화 1] {PROCESSING}" in text
    assert "쌍: A-B" in text


def test_an_answer_maps_letters_back_to_mentions() -> None:
    _, lettered = render([QUESTION])

    parsed = parse('{"answers": {"1": [["B", "blocked_by", "A"]]}}', [QUESTION], lettered)

    assert parsed == {1: frozenset({("정렬 로직", "blocked_by", "캐시")})}


@pytest.mark.parametrize("key", ["발화 1", "[발화 1]"])
def test_a_key_is_read_for_its_line_number(key: str) -> None:
    """The model echoes the ``[발화 1]`` label as the key. Reading only a bare
    digit dropped the answer and the line stated nothing."""
    _, lettered = render([QUESTION])

    answer = json.dumps({"answers": {key: [["B", "blocked_by", "A"]]}}, ensure_ascii=False)

    assert parse(answer, [QUESTION], lettered) == {
        1: frozenset({("정렬 로직", "blocked_by", "캐시")})
    }


def test_an_answer_that_names_no_line_asked_is_refused() -> None:
    _, lettered = render([QUESTION])

    with pytest.raises(ValueError):
        parse('{"answers": {"첫째": [["B", "blocked_by", "A"]]}}', [QUESTION], lettered)


def test_an_answer_whose_values_are_not_lists_is_refused() -> None:
    """The rules' answer stands for the batch, rather than a line read as
    stating nothing (#503 review)."""
    _, lettered = render([QUESTION])

    with pytest.raises(ValueError):
        parse('{"answers": {"1": "B blocked_by A"}}', [QUESTION], lettered)


@pytest.mark.parametrize(
    "stated",
    [
        [["B", "causes", "A"]],  # not a label
        [["A", "blocked_by", "C"]],  # a letter the line does not have
        [["A", "part_of", "A"]],  # a self-loop
        ["B blocked_by A"],  # not a triple
    ],
)
def test_what_the_line_did_not_offer_is_dropped(stated: list[Any]) -> None:
    _, lettered = render([QUESTION])

    answer = json.dumps({"answers": {"1": stated}})

    assert parse(answer, [QUESTION], lettered) == {1: frozenset()}


def test_an_answer_naming_the_mentions_is_read_like_letters() -> None:
    """What Gemini actually sent back on every probe, asked for letters."""
    _, lettered = render([QUESTION])

    parsed = parse(
        '{"answers": {"1": [["정렬 로직", "blocked_by", "캐시"], ["캐시", "part_of", "검색"]]}}',
        [QUESTION],
        lettered,
    )

    assert parsed == {1: frozenset({("정렬 로직", "blocked_by", "캐시")})}


def test_a_topic_named_like_another_mentions_letter_is_refused() -> None:
    """``B`` is the letter of 정렬 로직 and the name of the topic lettered ``A``.
    Either reading is possible, so neither is taken."""
    lettered_b = PairQuestion("B 때문에 정렬 로직이 막혀 있습니다", (("B", "정렬 로직"),))
    _, lettered = render([lettered_b])

    parsed = parse('{"answers": {"1": [["정렬 로직", "blocked_by", "B"]]}}', [lettered_b], lettered)

    assert lettered == {1: {"A": "B", "B": "정렬 로직"}}
    assert parsed == {1: frozenset()}


def test_a_mention_outside_the_offered_pairs_is_not_related() -> None:
    three = PairQuestion("A B C", (("가", "나"), ("나", "다")))
    _, lettered = render([three])

    parsed = parse('{"answers": {"1": [["A", "depends_on", "C"]]}}', [three], lettered)

    assert parsed == {1: frozenset()}


def test_an_answer_with_no_json_is_refused() -> None:
    _, lettered = render([QUESTION])

    with pytest.raises(ValueError):
        parse("관계 없음", [QUESTION], lettered)


def test_questions_are_batched_under_the_budget() -> None:
    many = [QUESTION] * 40

    assert all(len(render(batch)[0]) <= 1000 for batch in batches(many, 1000))
    assert sum(len(batch) for batch in batches(many, 1000)) == 40


# --- the Gemini client ----------------------------------------------------------------


def gemini(handler: Any) -> GeminiRelationAsker:
    asker = GeminiRelationAsker(
        api_key="test-key",
        model="gemini-test",
        base_url="https://example.invalid/v1beta",
        timeout_sec=5,
    )
    asker._client._client = httpx.Client(  # noqa: SLF001 - swap the transport only
        base_url="https://example.invalid/v1beta",
        transport=httpx.MockTransport(handler),
        headers={"x-goog-api-key": "test-key"},
    )
    return asker


def reply(text: str) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def test_the_request_carries_the_asked_line_and_the_key_only_in_a_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return reply('{"answers": {"1": [["B", "blocked_by", "A"]]}}')

    answers = gemini(handler).ask([QUESTION])

    assert answers == [frozenset({("정렬 로직", "blocked_by", "캐시")})]
    request = seen[0]
    assert request.url.path.endswith("/models/gemini-test:generateContent")
    assert request.headers["x-goog-api-key"] == "test-key"
    assert "test-key" not in request.content.decode()
    body = json.loads(request.content)
    assert body["systemInstruction"]["parts"][0]["text"] == INSTRUCTIONS
    assert PROCESSING in body["contents"][0]["parts"][0]["text"]
    assert len(json.dumps(body, ensure_ascii=False)) <= MAX_OUTBOUND_CHARS


def test_a_failed_request_leaves_its_questions_unanswered() -> None:
    assert gemini(lambda request: httpx.Response(400, json={})).ask([QUESTION, OTHER]) == [
        None,
        None,
    ]


def test_an_unmasked_phone_number_is_refused_and_raised() -> None:
    """Raised, not answered: an unmasked value in a stored transcript is module
    A's broken invariant, and falling back to the rules would hide it."""
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return reply('{"answers": {}}')

    leaked = PairQuestion("캐시는 010-1234-5678로 문의했는데 검색 기능은", (("캐시", "검색 기능"),))

    with pytest.raises(PrivacyViolationError):
        gemini(handler).ask([leaked])
    assert sent == []


def test_gemini_refuses_to_start_without_a_key() -> None:
    with pytest.raises(ValueError, match="AUTUNE_GAP_RELATION_IMPL"):
        GeminiRelationAsker(
            api_key="", model="m", base_url="https://example.invalid", timeout_sec=1
        )


# --- the registry (the entry set itself is pinned in test_pipeline_interfaces) --------


@pytest.fixture
def settings_restored() -> Any:
    settings = get_settings()
    saved = (settings.relation_impl, settings.verifier_api_key)
    reset_cache()
    yield settings
    settings.relation_impl, settings.verifier_api_key = saved
    reset_cache()


def test_the_rules_alone_are_the_default(settings_restored: Any) -> None:
    assert type(get_relation_extractor()) is RuleRelations


def test_gemini_is_the_rules_plus_the_asker(settings_restored: Any) -> None:
    settings_restored.relation_impl = "gemini"
    settings_restored.verifier_api_key = "test-key"

    extractor = get_relation_extractor()

    assert isinstance(extractor, AssistedRelations)
    assert extractor.model_version.startswith("rules-3+gemini:")
