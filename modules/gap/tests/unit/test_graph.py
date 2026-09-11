"""What the topic graph is, decided without a database or a model.

Every rule here is a property of ``autune_gap.graph``'s pure functions. The
integration tests check that ``service`` stores what these return; this file is
where what they return is argued about.
"""

from __future__ import annotations

import pytest

from autune_gap.graph import (
    CO_OCCURS,
    LABEL_MAX,
    Topic,
    build_topics,
    centrality,
    co_occurrence_edges,
    participation,
    topic_key,
)
from autune_gap.pipeline import Entity


def entity(text: str, utterance_id: str, label: str = "feature") -> Entity:
    return Entity(text=text, label=label, utterance_id=utterance_id)


def topic(key: str, *utterance_ids: str) -> Topic:
    return Topic(key=key, label=key, utterance_ids=utterance_ids)


# --- what a topic is --------------------------------------------------------


def test_mentions_of_the_same_thing_are_one_topic() -> None:
    topics = build_topics([entity("검색", "utt_1"), entity("검색", "utt_2")], ["utt_1", "utt_2"])

    assert [t.utterance_ids for t in topics] == [("utt_1", "utt_2")]


def test_spacing_and_case_do_not_split_a_topic() -> None:
    assert topic_key("검색  기능") == topic_key("검색 기능")
    assert topic_key("API") == topic_key("api")


def test_a_shorter_name_is_not_merged_into_a_longer_one() -> None:
    """Merging is a judgement about meaning. A wrong one hides a topic inside
    another, and the report then says the meeting covered what it did not."""
    topics = build_topics(
        [entity("검색", "utt_1"), entity("검색 기능", "utt_2")], ["utt_1", "utt_2"]
    )

    assert len(topics) == 2


def test_a_topic_is_labelled_the_way_the_meeting_first_said_it() -> None:
    topics = build_topics(
        [entity("API", "utt_2", "system"), entity("api", "utt_1", "system")], ["utt_1", "utt_2"]
    )

    assert topics[0].label == "api"


def test_a_label_fits_the_column() -> None:
    topics = build_topics([entity("가" * (LABEL_MAX + 50), "utt_1")], ["utt_1"])

    assert len(topics[0].label) == LABEL_MAX


def test_topics_come_in_order_of_first_mention() -> None:
    topics = build_topics(
        [entity("캐시", "utt_2", "system"), entity("검색", "utt_1")], ["utt_1", "utt_2"]
    )

    assert [t.key for t in topics] == ["검색", "캐시"]


def test_an_utterance_counts_once_for_a_topic() -> None:
    topics = build_topics([entity("검색", "utt_1"), entity("검색", "utt_1")], ["utt_1"])

    assert topics[0].utterance_ids == ("utt_1",)


def test_speech_left_out_of_the_analysis_builds_no_topic() -> None:
    """An utterance missing from the order was excluded on purpose — a
    participant who did not consent. A topic built from it would put their
    words back into a report the whole team reads."""
    topics = build_topics([entity("결제", "utt_excluded")], ["utt_1"])

    assert topics == []


def test_what_is_left_of_masked_data_is_not_a_topic() -> None:
    """``010-****-5678`` keeps its last four digits by design (privacy.md
    section 2). As a graph node it would carry them into the report."""
    topics = build_topics([entity("010-****-5678", "utt_1", "metric")], ["utt_1"])

    assert topics == []


def test_a_meeting_with_nothing_named_has_no_topics() -> None:
    assert build_topics([], ["utt_1"]) == []


# --- edges ------------------------------------------------------------------


def test_topics_named_together_are_joined_both_ways() -> None:
    """Directed rows, because the triples #32 produces will be directed. A
    co-occurrence has no direction, so it is written as two."""
    edges = co_occurrence_edges([topic("검색", "utt_1"), topic("캐시", "utt_1")])

    assert {(e.source, e.target) for e in edges} == {("검색", "캐시"), ("캐시", "검색")}
    assert {e.relation for e in edges} == {CO_OCCURS}


def test_topics_never_named_together_are_not_joined() -> None:
    assert co_occurrence_edges([topic("검색", "utt_1"), topic("캐시", "utt_2")]) == []


def test_the_strongest_pair_weighs_one_and_the_rest_less() -> None:
    """``gap_topic_edges`` accepts ``(0, 1]``."""
    edges = co_occurrence_edges(
        [
            topic("검색", "utt_1", "utt_2"),
            topic("캐시", "utt_1", "utt_2", "utt_3"),
            topic("배치", "utt_3"),
        ]
    )
    weight = {(e.source, e.target): e.weight for e in edges}

    assert weight[("검색", "캐시")] == 1.0
    assert weight[("배치", "캐시")] == 0.5


def test_no_topic_is_joined_to_itself() -> None:
    edges = co_occurrence_edges([topic("검색", "utt_1", "utt_2")])

    assert all(e.source != e.target for e in edges)


# --- centrality -------------------------------------------------------------


def test_the_topic_that_carried_the_meeting_scores_one() -> None:
    topics = [topic("검색", "utt_1", "utt_2"), topic("캐시", "utt_2")]
    scores = centrality(topics, co_occurrence_edges(topics))

    assert max(s.pagerank for s in scores.values()) == 1.0


def test_every_score_is_in_the_range_the_table_accepts() -> None:
    topics = [topic(k, *u) for k, u in {"a": ("u1", "u2"), "b": ("u2",), "c": ("u3",)}.items()]
    scores = centrality(topics, co_occurrence_edges(topics))

    for score in scores.values():
        assert 0.0 <= score.pagerank <= 1.0
        assert 0.0 <= score.betweenness <= 1.0


def test_without_edges_the_topic_named_more_often_ranks_higher() -> None:
    """Until #32 most topics share no utterance. Plain PageRank would rank a
    topic named once level with one named forty times."""
    topics = [topic("검색", "u1", "u2", "u3"), topic("캐시", "u4")]
    scores = centrality(topics, [])

    assert scores["검색"].pagerank > scores["캐시"].pagerank


def test_the_topic_bridging_two_others_has_the_highest_betweenness() -> None:
    topics = [topic("검색", "u1"), topic("캐시", "u1", "u2"), topic("배치", "u2")]
    scores = centrality(topics, co_occurrence_edges(topics))

    assert scores["캐시"].betweenness == max(s.betweenness for s in scores.values())
    assert scores["캐시"].betweenness > 0


def test_a_meeting_with_no_topics_has_no_scores() -> None:
    assert centrality([], []) == {}


# --- participation ----------------------------------------------------------


def test_participation_says_who_spoke_on_a_topic_and_who_did_not() -> None:
    matrix = participation(
        topic("검색", "u1", "u2"),
        speaker_of={"u1": "prt_a", "u2": "prt_a", "u3": "prt_b"},
        participants=["prt_a", "prt_b"],
    )

    assert matrix == {"prt_a": True, "prt_b": False}


def test_a_participant_who_never_spoke_is_recorded_as_silent() -> None:
    """Silence is the evidence a gap is raised on; a participant with no entry
    is one nobody considered, which is not the same thing."""
    matrix = participation(topic("검색", "u1"), {"u1": "prt_a"}, ["prt_a", "prt_c"])

    assert matrix["prt_c"] is False


@pytest.mark.parametrize("mentions", [1, 7])
def test_participation_is_a_yes_or_no_however_much_was_said(mentions: int) -> None:
    """A count here is a speaking ratio by another name (privacy.md section 3)."""
    utterances = tuple(f"u{i}" for i in range(mentions))
    matrix = participation(topic("검색", *utterances), {u: "prt_a" for u in utterances}, ["prt_a"])

    assert matrix == {"prt_a": True}
