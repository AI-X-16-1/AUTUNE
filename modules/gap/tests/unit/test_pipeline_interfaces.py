"""The entity-extraction seam: what an entity promises, and what has no
implementation.

No weights, no network, no corpus. The model is #13's other half; this is the
shape the topic graph is built against, and it is testable now.
"""

from __future__ import annotations

import pytest

from autune_gap.config import get_settings
from autune_gap.pipeline import (
    ENTITY_LABELS,
    RELATION_LABELS,
    Entity,
    EntityExtractor,
    FakeNer,
    RelationExtractor,
    RuleRelations,
)
from autune_gap.pipeline.ner import _IGNORED_LABELS, _SPACY_LABELS, _claim_spans
from autune_gap.pipeline.registry import (
    _EXTRACTORS,
    _RELATION_EXTRACTORS,
    get_relation_extractor,
    reset_cache,
)

KO_CORE_NEWS_LG_NER = frozenset({"DT", "LC", "OG", "PS", "QT", "TI"})
"""The NER inventory ``ko_core_news_lg`` 3.8.0 declares in its own ``meta``.

Written down here rather than read from the model: the pipeline is a 220MB
wheel in an optional extra, and this file must run without it. Read back with

    uv run --package autune-gap --extra local-models python -c \\
        "import spacy; print(sorted(spacy.load('ko_core_news_lg').meta['labels']['ner']))"
"""

# --- what an entity means ---------------------------------------------------


def test_an_entity_names_the_utterance_it_came_from() -> None:
    """Without it the report can show a topic but not why it thinks the meeting
    discussed it, and 근거 발화 보기 has nothing to open."""
    entity = Entity(text="검색 기능", label="feature", utterance_id="utt_1")

    assert entity.utterance_id == "utt_1"


def test_an_unknown_label_is_refused() -> None:
    """The five labels are the graph's vocabulary. A sixth arriving from a model
    that was swapped underneath would put a node in the graph that nothing
    downstream knows how to weight."""
    with pytest.raises(ValueError, match="unknown entity label"):
        Entity(text="어딘가", label="ORG", utterance_id="utt_1")


def test_the_label_set_is_the_one_the_module_doc_names() -> None:
    """Five from step 1, plus ``term`` for a compound whose kind is undecided —
    which is every compound until #13 trains a model that can tell a feature
    from a system. See ``pipeline.spoken``."""
    assert set(ENTITY_LABELS) == {"feature", "system", "metric", "person", "date", "term"}


def test_an_entity_is_comparable_by_value() -> None:
    """Frozen and equatable, so a test can state a whole expected entity."""
    assert Entity("검색", "feature", "utt_1") == Entity("검색", "feature", "utt_1")


# --- what has deliberately no implementation --------------------------------


def test_there_is_no_external_extractor() -> None:
    """Extraction runs over every utterance of a meeting, so an external option
    means handing over the whole transcript — privacy.md section 6 makes that a
    design conversation, not a config string.

    Asserted as the whole key set so adding one fails here, where the reason is
    written down, rather than passing as an ordinary feature.
    """
    assert set(_EXTRACTORS) == {"spacy", "fake"}


def test_the_relation_step_has_one_implementation_and_a_seam() -> None:
    """Step 2 is the step promised LLM assistance for its hard cases (#32), and
    unlike entity extraction it may have it — a relation needs the clause, not
    the transcript. The seam is here so the second entry has somewhere to go;
    the assertion is whole so adding one is a decision somebody made, not a
    dictionary key that appeared.
    """
    assert set(_RELATION_EXTRACTORS) == {"rule"}


def test_the_rule_extractor_satisfies_the_protocol() -> None:
    assert isinstance(RuleRelations(), RelationExtractor)


def test_the_configured_relation_extractor_is_the_rule_one() -> None:
    reset_cache()

    assert isinstance(get_relation_extractor(), RuleRelations)


def test_an_unknown_relation_implementation_is_named_in_the_error() -> None:
    """The first person to mistype the variable reads this message."""
    reset_cache()
    settings = get_settings()
    original = settings.relation_impl
    settings.relation_impl = "llm"
    try:
        with pytest.raises(ValueError, match="AUTUNE_GAP_RELATION_IMPL"):
            get_relation_extractor()
    finally:
        settings.relation_impl = original
        reset_cache()


def test_the_relation_vocabulary_is_the_one_the_module_doc_names() -> None:
    """Four relations, each of which changes what risk scoring (#35) should do
    with the pair. ``co_occurs`` is not one of them: nothing extracted it."""
    assert set(RELATION_LABELS) == {"depends_on", "blocked_by", "part_of", "alternative_to"}


# --- every label the model emits is decided about ---------------------------


def test_every_label_the_model_emits_is_mapped_or_ignored() -> None:
    """An unmapped label yields no entity, not an error.

    ``TI`` was missing from the first version of the map, so "오후 3시" was
    dropped and nothing said so. Accounting for the whole inventory is what
    makes a forgotten label look different from a rejected one — the argument
    module B wrote down for ``EXCLUDED_ACTS``.
    """
    accounted = set(_SPACY_LABELS) | set(_IGNORED_LABELS)

    assert accounted == KO_CORE_NEWS_LG_NER


def test_the_two_maps_do_not_overlap() -> None:
    """A label cannot be both used and refused."""
    assert not (_SPACY_LABELS.keys() & _IGNORED_LABELS.keys())


def test_every_ignored_label_says_why() -> None:
    """A label left out silently reads as an oversight a year from now."""
    assert all(reason.strip() for reason in _IGNORED_LABELS.values())


def test_every_mapped_label_lands_in_the_vocabulary() -> None:
    assert set(_SPACY_LABELS.values()) <= set(ENTITY_LABELS)


def test_a_time_is_a_date() -> None:
    """ "다음 주 화요일" and "오후 3시" are both when-something-happens, and
    nothing downstream weights them differently."""
    assert _SPACY_LABELS["TI"] == _SPACY_LABELS["DT"] == "date"


# --- the fake, which everything downstream is built on ----------------------


def test_the_fake_satisfies_the_protocol() -> None:
    """It stands in for the real extractor everywhere downstream, so it has to
    be substitutable for it, not merely similar."""
    assert isinstance(FakeNer(), EntityExtractor)


def test_an_empty_meeting_is_not_an_error() -> None:
    """A meeting can have nothing left after filtering, and that is not a
    failure."""
    assert FakeNer().extract([]) == []


def test_the_fake_names_itself() -> None:
    """model_version is recorded with the rows this produces; a graph that
    cannot be attributed to a version cannot be compared against the next."""
    assert FakeNer().model_version == "fake"


def test_every_entity_carries_a_known_label() -> None:
    found = FakeNer().extract(
        [
            ("utt_1", "검색 기능 응답이 3초 걸립니다"),
            ("utt_2", "인덱스를 다음 주에 다시 만들죠"),
        ]
    )

    assert found
    assert all(entity.label in ENTITY_LABELS for entity in found)


def test_entities_are_attributed_to_the_right_utterance() -> None:
    found = FakeNer().extract([("utt_1", "검색 기능"), ("utt_2", "캐시")])
    by_id = {entity.utterance_id: entity.label for entity in found}

    assert by_id == {"utt_1": "feature", "utt_2": "system"}


def test_the_result_is_flat_and_not_one_list_per_utterance() -> None:
    """Every entity already names its utterance, so nesting would make "every
    feature in this meeting" a flatten at every call site."""
    found = FakeNer().extract([("utt_1", "검색 기능과 정렬 기능")])

    assert all(isinstance(entity, Entity) for entity in found)
    assert len(found) == 2


# --- one character belongs to one entity ------------------------------------


def test_a_date_is_not_also_a_metric() -> None:
    """Both patterns start at a digit. Without span arbitration "3월 1일" would
    be claimed twice and the graph would grow a node nobody can point at."""
    claimed = _claim_spans("3월 1일까지 하겠습니다")

    assert claimed == [("3월 1일", "date")]


def test_spans_come_back_in_the_order_they_were_said() -> None:
    """Patterns are checked most-specific-first, which is not reading order.
    The caller reads a transcript, so the result has to be in transcript
    order."""
    claimed = _claim_spans("검색 기능이 3초 걸려서 캐시를 봤습니다")

    assert [span for span, _ in claimed] == ["검색 기능", "3초", "캐시"]


def test_a_meeting_that_mentions_nothing_yields_nothing() -> None:
    assert _claim_spans("네 알겠습니다") == []
