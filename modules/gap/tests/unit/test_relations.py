"""What the rules assert, and what they refuse to guess.

No model. ``relations_in`` is handed the mentions directly, which is how a rule
gets argued with: the entity extractor's recall is #13's problem, and a rule
that can only be exercised through a 220MB pipeline is a rule nobody tunes.

The measurement over the real pipeline lives in ``test_spacy_ner.py``.
"""

from __future__ import annotations

import pytest

from autune_gap.pipeline import RELATION_LABELS, SYMMETRIC_RELATIONS, Entity, Relation
from autune_gap.pipeline.relations import (
    MAX_MARKER_DISTANCE,
    RuleRelations,
    mention_spans,
    relations_in,
)


def triples(text: str, *mentions: str) -> list[tuple[str, str, str]]:
    """The relations ``text`` asserts, given the names the meeting uses."""
    return relations_in(text, mention_spans(text, mentions))


# --- where a mention is -----------------------------------------------------


def test_a_mention_is_found_where_the_speaker_said_it() -> None:
    spans = mention_spans("검색 기능은 캐시를 씁니다", ["검색 기능", "캐시"])

    assert [(span.text, span.start) for span in spans] == [("검색 기능", 0), ("캐시", 7)]


def test_a_mention_is_found_under_a_particle() -> None:
    """The utterance that states a relation is the one where the topic wears a
    particle — 실시간은 — and the bare form is what entity extraction claimed
    somewhere else. Keyed on exact tokens the rules would see one end of every
    relation and never the other."""
    spans = mention_spans("실시간은 콜드스타트가 문제입니다", ["실시간", "콜드스타트"])

    assert [span.text for span in spans] == ["실시간", "콜드스타트"]


def test_spacing_does_not_hide_a_mention() -> None:
    """``noun_terms`` joins a run with single spaces; ASR output carries double
    ones. A literal search would find neither the mention nor its relation."""
    spans = mention_spans("검색  개인화 기능은 캐시가 필요합니다", ["검색 개인화 기능", "캐시"])

    assert [span.text for span in spans] == ["검색 개인화 기능", "캐시"]


def test_a_longer_name_claims_the_characters_a_shorter_one_would() -> None:
    """Otherwise 정렬 is a second mention inside 인기순 정렬, and a rule keyed on
    distance relates a topic to itself."""
    spans = mention_spans("인기순 정렬은 캐시가 필요합니다", ["인기순 정렬", "정렬", "캐시"])

    assert [span.text for span in spans] == ["인기순 정렬", "캐시"]


def test_a_name_inside_a_longer_word_is_not_a_mention() -> None:
    """``실시간`` sits inside ``비실시간``, and the utterance that contains those
    characters negates the topic they name: "비실시간 처리가 필요해서 검색
    기능은 미뤘습니다" asserted 검색 기능 depends_on 실시간.

    Only the left side is guarded — Korean attaches particles directly, so
    실시간은 and 실시간으로 have to stay mentions. Raised in review of #249.
    """
    spans = mention_spans(
        "비실시간 처리가 필요해서 검색 기능은 미뤘습니다", ["실시간", "검색 기능"]
    )

    assert [span.text for span in spans] == ["검색 기능"]


def test_a_particle_on_the_right_still_leaves_a_mention() -> None:
    spans = mention_spans("실시간은 실시간으로 하죠", ["실시간"])

    assert [span.start for span in spans] == [0, 5]


def test_a_name_said_twice_is_two_mentions() -> None:
    """Proximity decides which mention a marker binds, so both occurrences have
    to be on the map."""
    spans = mention_spans("캐시 얘기를 했는데 캐시가 문제입니다", ["캐시"])

    assert [span.start for span in spans] == [0, 11]


# --- depends_on -------------------------------------------------------------


def test_a_need_makes_the_thing_needed_the_target() -> None:
    assert triples("정렬 로직은 인덱스가 필요합니다", "정렬 로직", "인덱스") == [
        ("정렬 로직", "인덱스", "depends_on")
    ]


def test_the_other_korean_word_order_reads_the_same_way() -> None:
    """ "인덱스가 있어야 정렬 로직을 붙입니다" puts the needed thing before the
    marker and the dependent after it. Both orders name what is needed
    immediately before the marker, which is why the rule anchors there."""
    assert triples("인덱스가 있어야 정렬 로직을 붙입니다", "정렬 로직", "인덱스") == [
        ("정렬 로직", "인덱스", "depends_on")
    ]


def test_a_need_with_only_one_topic_asserts_nothing() -> None:
    """Something is needed and the utterance does not say what for. A rule that
    guessed would attach the dependency to whatever topic came last."""
    assert triples("인덱스가 필요합니다", "인덱스") == []


def test_the_thing_needed_has_to_be_beside_the_marker() -> None:
    """A marker in the third clause says nothing about a topic in the first.
    ``인덱스`` is what 필요 binds, and here it is a whole discussion away."""
    far = (
        "인덱스는 "
        + "여러 가지를 한참 논의했고 결론이 없었습니다만 어쨌든 "
        + "정렬 로직이 필요합니다"
    )

    assert len(far) > MAX_MARKER_DISTANCE
    assert triples(far, "인덱스") == []


def test_the_topic_the_sentence_is_about_may_be_further_back() -> None:
    """Only the *target* — the thing needed — has to sit beside the marker. The
    other end is what the sentence is about, and Korean marks that at the front
    with 은/는 and then says everything else: "정렬 로직은 (한참) 인덱스가
    필요합니다" is one clause about 정렬 로직 however long it runs.
    """
    long = (
        "정렬 로직은 "
        + "여러 가지를 한참 논의했고 결론이 없었습니다만 어쨌든 "
        + "인덱스가 필요합니다"
    )

    assert len(long) > MAX_MARKER_DISTANCE
    assert triples(long, "정렬 로직", "인덱스") == [("정렬 로직", "인덱스", "depends_on")]


def test_a_topic_does_not_depend_on_itself() -> None:
    """A speaker restarting a sentence is not a dependency, and
    ``gap_topic_edges`` forbids the self-loop anyway."""
    assert triples("검색 기능은 검색 기능이 필요해서 미뤘습니다", "검색 기능") == []


# --- a marker is a string; the clause decides whether it was meant -----------
#
# Every sentence in this section came back with an edge before the guards, and
# each one was found by running the extractor rather than by reading it. Raised
# in review of #249.


def test_a_need_that_was_denied_is_not_a_dependency() -> None:
    assert triples("정렬 로직은 인덱스가 필요 없습니다", "정렬 로직", "인덱스") == []


def test_a_need_denied_the_long_way_round_is_not_one_either() -> None:
    assert triples("정렬 로직은 인덱스가 필요하지 않아요", "정렬 로직", "인덱스") == []


def test_a_need_that_was_asked_about_is_not_an_answer() -> None:
    """The meeting has not said the dependency exists. Recording the question as
    the answer reports a dependency nobody asserted."""
    assert triples("정렬 로직은 인덱스가 필요한가요?", "정렬 로직", "인덱스") == []


def test_a_denial_in_the_next_clause_does_not_cancel_a_need() -> None:
    """The guard reads one clause, not the utterance. 문제 없습니다 is about
    캐시 and says nothing about what 정렬 로직 needs."""
    assert triples(
        "정렬 로직은 인덱스가 필요하고 캐시는 문제 없습니다", "정렬 로직", "인덱스", "캐시"
    ) == [("정렬 로직", "인덱스", "depends_on")]


def test_a_blocker_that_is_not_there_blocks_nothing() -> None:
    """ "캐시 이슈는 없어서" is a blocker word, a causal connective, and no
    blocker. It read as 검색 기능 blocked_by 캐시 — the exact reverse of what the
    speaker said, in the one relation the report treats as a finding."""
    assert triples("캐시 이슈는 없어서 검색 기능은 바로 진행합니다", "캐시", "검색 기능") == []


def test_a_blocker_that_was_resolved_blocks_nothing() -> None:
    """ "해결되어서" is a blocker word, a causal connective, and the opposite of
    a blocker. It is the positively phrased twin of "이슈는 없어서", and it put
    the reverse of what the speaker said into the one relation C treats as a
    finding. Raised in review of #249."""
    assert triples("캐시 이슈가 해결되어서 검색 기능은 바로 진행합니다", "캐시", "검색 기능") == []


@pytest.mark.parametrize(
    "text",
    [
        "캐시 이슈가 해소되어서 검색 기능은 바로 진행합니다",
        "캐시 이슈가 정리되어서 검색 기능은 이번 주에 붙입니다",
        "캐시 이슈는 어제 처리해서 검색 기능은 바로 진행합니다",
        "캐시 이슈가 풀려서 검색 기능은 바로 진행합니다",
    ],
)
def test_the_other_ways_of_saying_it_is_gone(text: str) -> None:
    assert triples(text, "캐시", "검색 기능") == []


@pytest.mark.parametrize(
    "text",
    [
        "검색 기능은 캐시 이슈 해결이 안 되어서 막혀 있습니다",
        "검색 기능은 캐시 이슈 처리가 안 되어서 막혀 있습니다",
    ],
)
def test_a_resolution_that_did_not_happen_leaves_the_blocker_standing(text: str) -> None:
    """해결 and 처리 are the words for doing the thing and for failing to. A
    resolution list matched as bare substrings would throw away the blockers
    that matter most — the ones somebody just said are not fixed."""
    assert triples(text, "캐시", "검색 기능") == [("검색 기능", "캐시", "blocked_by")]


def test_a_need_quoted_in_a_question_is_still_a_question() -> None:
    """``-다고`` puts a 고 inside one clause. Read as a clause break it cut the
    window before the 나요, and the question came back as an asserted
    dependency. Raised in review of #249."""
    assert triples("정렬 로직은 인덱스가 필요하다고 보시나요", "정렬 로직", "인덱스") == []


def test_a_plain_connective_still_ends_a_clause() -> None:
    """The quotative exception is 다고 and nothing wider: 필요하고 still ends the
    clause, which is what keeps the next subject out of the relation."""
    assert triples(
        "정렬 로직은 인덱스가 필요하고 캐시는 다음 주에 봅시다",
        "정렬 로직",
        "인덱스",
        "캐시",
    ) == [("정렬 로직", "인덱스", "depends_on")]


def test_the_same_utterance_claims_the_same_mentions_every_run() -> None:
    """Ties used to fall back to set iteration order, which `PYTHONHASHSEED`
    randomises per process, so a worker restart changed the graph a re-processed
    meeting came back with. Both names are five characters and both match over
    overlapping spans, which is the case that varied. Raised in review of #249.
    """
    text = "검색 기능 개선 이야기입니다"
    first = mention_spans(text, ["검색 기능", "기능 개선"])
    for _ in range(50):
        assert [(m.text, m.start) for m in mention_spans(text, ["기능 개선", "검색 기능"])] == [
            (m.text, m.start) for m in first
        ]


def test_a_reason_two_clauses_away_is_somebody_elses_reason() -> None:
    """The causal connective has to be in the blocker's own clause. Searching to
    the end of the utterance paired 이슈 with a 없어서 belonging to another
    sentence, and put a date on the blocked end."""
    assert (
        triples(
            "로그인 모듈 이슈는 어제 처리했고요 결제 모듈 얘기는 시간이 없어서 짧게 할게요",
            "로그인 모듈",
            "결제 모듈",
            "어제",
        )
        == []
    )


def test_the_next_clauses_subject_is_not_the_one_that_needs_something() -> None:
    """Korean puts a new subject after a connective ending, so the nearest
    mention after the marker is usually the next sentence. It read as 캐시
    depends_on 인덱스."""
    assert triples(
        "정렬 로직은 인덱스가 필요하고 캐시는 다음 주에 봅시다", "정렬 로직", "인덱스", "캐시"
    ) == [("정렬 로직", "인덱스", "depends_on")]


def test_the_source_is_what_the_sentence_is_about() -> None:
    """Not simply the mention before the target: here that is 인덱스, sitting
    inside the clause the sentence has just denied. 정렬 로직 wears 은."""
    assert triples(
        "정렬 로직은 인덱스에 의존하지 않고 캐시 없이는 안 됩니다", "정렬 로직", "인덱스", "캐시"
    ) == [("정렬 로직", "캐시", "depends_on")]


def test_a_need_stated_through_a_negative_still_counts() -> None:
    """ "캐시 없이는 안 됩니다" is a need. 안 is not a negation marker here for
    that reason, and the cue carries its own 없."""
    assert triples("정렬 로직은 캐시 없이는 안 됩니다", "정렬 로직", "캐시") == [
        ("정렬 로직", "캐시", "depends_on")
    ]


# --- blocked_by -------------------------------------------------------------


def test_a_blocker_offered_as_a_reason_is_a_relation() -> None:
    """The one relation that is itself a finding: something was named out loud
    as the thing in the way."""
    assert triples("실시간은 콜드스타트가 안 잡혀 있어서 무리입니다", "실시간", "콜드스타트") == [
        ("실시간", "콜드스타트", "blocked_by")
    ]


def test_a_blocker_with_no_reason_given_is_not_a_relation() -> None:
    """ "콜드스타트가 안 잡혀 있습니다" is a status report. Which topic that
    state is about is not something a rule can read, and reading it as a blocker
    would put a blocker in the report nobody said."""
    assert (
        triples("실시간 얘기를 했습니다. 콜드스타트가 안 잡혀 있습니다", "실시간", "콜드스타트")
        == []
    )


def test_a_reason_stated_before_the_blocker_is_still_a_reason() -> None:
    """#254. Korean puts 때문에 and 탓에 ahead of the predicate they explain, so
    the connective sits *behind* the cue and the forward-only window never saw
    it. The relation the meeting stated — 검색 기능 blocked_by 캐시 — was
    dropped."""
    assert triples("검색 기능은 캐시 때문에 막혀 있습니다", "검색 기능", "캐시") == [
        ("검색 기능", "캐시", "blocked_by")
    ]
    assert triples("결제 모듈은 인증 탓에 막혀 있습니다", "결제 모듈", "인증") == [
        ("결제 모듈", "인증", "blocked_by")
    ]


def test_the_thing_in_the_way_is_what_the_reason_clause_names() -> None:
    """The marker is the connective rather than the cue, which is what keeps the
    two ends apart when both are named: 정렬 로직 is what is blocked and sits
    between 때문에 and 막혀, so reading the mention before the *cue* would have
    made it block on itself."""
    assert triples("캐시 때문에 정렬 로직이 막혀 있습니다", "캐시", "정렬 로직") == [
        ("정렬 로직", "캐시", "blocked_by")
    ]


def test_a_reason_behind_the_cue_still_has_to_be_the_same_clause() -> None:
    """The bound that #249 added, read backwards. A connective in the previous
    clause belongs to the previous clause's subject, and the whole point of the
    window is that widening it broke this."""
    assert (
        triples(
            "로그인 모듈은 시간이 없어서 못 했고요 결제 모듈 이슈는 남아 있습니다",
            "로그인 모듈",
            "결제 모듈",
        )
        == []
    )


def test_a_blocker_read_from_both_sides_is_asserted_once() -> None:
    """ "안 잡혀 있어서" carries a connective after 안 잡 and before 무리, so
    both readings fire on one sentence. They agree, and a pair never comes back
    twice with the same relation."""
    assert triples("실시간은 콜드스타트가 안 잡혀 있어서 무리입니다", "실시간", "콜드스타트") == [
        ("실시간", "콜드스타트", "blocked_by")
    ]


def test_a_resolution_in_the_reason_clause_leaves_nothing_blocked() -> None:
    """The guard that already applied to a reason in front of the cue applies to
    one behind it.

    "캐시 이슈가 해결됐기 때문에 …" is the reason something *proceeds*, and
    reading it as a blocker would put the reverse of what the meeting said into
    the report — which is what ``_resolved`` exists for and why ``blocked_by``
    is the relation it guards. Both cues in this sentence are refused: 이슈 by
    the reason in front of it, 막혀 by the one behind.
    """
    assert (
        triples(
            "캐시 이슈가 해결됐기 때문에 검색 기능은 막혀 있던 게 풀립니다", "캐시", "검색 기능"
        )
        == []
    )


def test_a_resolution_word_used_as_a_noun_costs_the_relation() -> None:
    """What the guard above costs, stated rather than left to be found.

    처리 is both "dealt with" and "the processing" — ``_RESOLVED`` already says
    so, and ``_UNDONE`` is what takes the first reading back. Neither can see
    that this one is a noun naming the work, so a reason clause that names it
    reads as a resolution and the blocker is dropped.

    Kept in the losing direction on purpose: C's metric is precision, a
    ``blocked_by`` is a finding in its own right, and a blocker the meeting did
    not assert costs more than one it did. The same ambiguity was already here
    in the forward direction; this only means it now costs a relation in two
    places instead of one. Telling the two readings apart needs the assisted
    implementation step 2 is promised, not a longer list.
    """
    assert triples("캐시 처리 때문에 검색 기능은 막혀 있습니다", "캐시", "검색 기능") == []


# --- part_of, which no rule produces ----------------------------------------


def test_a_genitive_is_not_read_as_a_part() -> None:
    """의 marks possession and composition with the same character.

    "검색의 정렬 로직" is a part of a thing and "검색 기능의 담당자 일정" is
    somebody's calendar, and nothing in the surface string tells them apart —
    the argument that keeps ``는데`` out of the contrast markers. The rule used
    to read both, and the second one put a person's schedule inside a feature.
    Raised in review of #249.
    """
    assert triples("검색의 정렬 로직을 봤습니다", "검색", "정렬 로직") == []
    assert triples("검색 기능의 담당자 일정을 봤습니다", "검색 기능", "담당자 일정") == []


# --- alternative_to ---------------------------------------------------------


def test_a_contrast_marker_joins_the_pair_both_ways() -> None:
    """Symmetric, so it is two rows: "A 대신 B" and "B 대신 A" are the same
    statement about the pair."""
    found = triples("인기순 정렬 대신 실시간 개인화로 가시죠", "인기순 정렬", "실시간 개인화")

    assert found == [
        ("인기순 정렬", "실시간 개인화", "alternative_to"),
        ("실시간 개인화", "인기순 정렬", "alternative_to"),
    ]


def test_a_latin_marker_needs_a_word_boundary() -> None:
    """ "API devs 검색 기능" contains ``vs`` inside ``devs``. A substring match
    made the two topics alternatives to each other on the strength of a plural.
    Raised in review of #249."""
    assert triples("API devs 검색 기능 얘기했습니다", "API", "검색 기능") == []


def test_a_latin_marker_still_reads_when_it_is_a_word() -> None:
    assert triples("인기순 정렬 vs 실시간 개인화 논의했습니다", "인기순 정렬", "실시간 개인화") == [
        ("인기순 정렬", "실시간 개인화", "alternative_to"),
        ("실시간 개인화", "인기순 정렬", "alternative_to"),
    ]


def test_sentence_glue_is_not_a_contrast() -> None:
    """``는데`` is how spoken Korean joins two clauses about anything at all. A
    marker that fires on every second utterance would make ``alternative_to``
    the most common relation in the graph and every one of them a coin flip."""
    assert triples("검색 기능 보는데 정렬 로직도 봐야 합니다", "검색 기능", "정렬 로직") == []


# --- one utterance, more than one relation ----------------------------------


def test_the_same_relation_is_not_asserted_twice() -> None:
    assert (
        triples("인덱스가 필요하고 인덱스가 필요합니다", "정렬 로직", "인덱스").count(
            ("정렬 로직", "인덱스", "depends_on")
        )
        <= 1
    )


def test_an_utterance_naming_one_topic_asserts_nothing() -> None:
    assert triples("캐시가 필요합니다", "캐시") == []


# --- the extractor over a meeting -------------------------------------------


def test_a_relation_is_attributed_to_the_utterance_that_stated_it() -> None:
    """A relation extracted from an utterance analysis later drops has to be
    droppable with it, and a rule that cannot say where it fired cannot be
    checked."""
    found = RuleRelations().extract(
        [
            ("utt_1", "실시간 개인화 얘기입니다"),
            ("utt_2", "실시간은 콜드스타트가 안 잡혀 있어서 무리입니다"),
        ],
        [
            Entity(text="실시간", label="term", utterance_id="utt_1"),
            Entity(text="콜드스타트", label="term", utterance_id="utt_2"),
        ],
    )

    assert found == [
        Relation(source="실시간", target="콜드스타트", relation="blocked_by", utterance_id="utt_2")
    ]


def test_a_name_from_another_utterance_is_still_a_name() -> None:
    """Entity extraction claims a bare noun run and stops at a particle, so the
    utterance that states the relation is usually not the one the topic was
    claimed in. Keyed on this utterance's own entities the rules found nothing
    at all on the shared fixtures."""
    found = RuleRelations().extract(
        [
            ("utt_1", "정렬 로직 보겠습니다"),
            ("utt_2", "인덱스 확인했습니다"),
            ("utt_3", "정렬 로직은 인덱스가 필요합니다"),
        ],
        [
            Entity(text="정렬 로직", label="term", utterance_id="utt_1"),
            Entity(text="인덱스", label="term", utterance_id="utt_2"),
        ],
    )

    assert [(r.source, r.target, r.relation) for r in found] == [
        ("정렬 로직", "인덱스", "depends_on")
    ]


def test_a_meeting_with_no_entities_yields_no_relations() -> None:
    assert RuleRelations().extract([("utt_1", "인덱스가 필요합니다")], []) == []


def test_the_extractor_names_itself() -> None:
    """What decides this implementation's output is the marker lists in
    ``relations.py``, and they change without anything else changing."""
    assert RuleRelations().model_version == "rules-2"


# --- the vocabulary ---------------------------------------------------------


def test_every_relation_the_rules_produce_is_in_the_vocabulary() -> None:
    """A relation label nothing downstream knows how to weight is a relation
    risk scoring (#35) will read as a finding it cannot rank."""
    found = triples("검색의 정렬 로직은 인덱스가 필요합니다", "검색", "정렬 로직", "인덱스")

    assert {relation for _, _, relation in found} <= set(RELATION_LABELS)


def test_an_unknown_relation_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown relation"):
        Relation(source="검색", target="캐시", relation="relates_to", utterance_id="utt_1")


def test_a_relation_needs_both_ends() -> None:
    with pytest.raises(ValueError, match="both ends"):
        Relation(source="검색", target="", relation="depends_on", utterance_id="utt_1")


def test_the_symmetric_relations_are_a_subset_of_the_vocabulary() -> None:
    assert set(RELATION_LABELS) >= SYMMETRIC_RELATIONS
