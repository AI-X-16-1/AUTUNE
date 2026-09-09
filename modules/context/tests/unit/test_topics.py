"""Topic segmentation and labelling — no model, no database."""

from __future__ import annotations

from autune_context.pipeline.topics import _label, extract_topics
from autune_contracts import Utterance


class _BlockEmbedder:
    """Returns one of two orthogonal vectors depending on a marker in the text."""

    dim = 4
    model_version = "block-stub"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] if "AAA" in t else [0.0, 1.0, 0.0, 0.0] for t in texts]


def _utterances(texts: list[str]) -> list[Utterance]:
    return [
        Utterance(
            id=f"utt_{i}", speaker="화자", start=float(i), end=float(i) + 1, text=t, confidence=0.9
        )
        for i, t in enumerate(texts)
    ]


def test_a_clean_topic_shift_becomes_one_boundary():
    utts = _utterances(["AAA 안건"] * 5 + ["BBB 안건"] * 5)
    segments = extract_topics(utts, _BlockEmbedder(), window=3, min_segment=3, depth_threshold=0.1)
    assert len(segments) == 2
    assert [s.utterance_ids for s in segments] == [
        ["utt_0", "utt_1", "utt_2", "utt_3", "utt_4"],
        ["utt_5", "utt_6", "utt_7", "utt_8", "utt_9"],
    ]


def test_a_single_topic_meeting_is_one_segment():
    segments = extract_topics(_utterances(["AAA"] * 8), _BlockEmbedder(), min_segment=3)
    assert len(segments) == 1
    assert len(segments[0].vector) == 4


def test_a_transcript_shorter_than_two_segments_is_not_split():
    segments = extract_topics(_utterances(["AAA", "BBB", "AAA"]), _BlockEmbedder(), min_segment=3)
    assert len(segments) == 1


def test_no_utterances_yields_no_topics():
    assert extract_topics([], _BlockEmbedder()) == []


def test_label_is_the_most_repeated_noun_phrase():
    assert _label("검색 개인화 논의. 검색 개인화 일정. 검색 개인화 담당자.") == "검색 개인화"


def test_label_falls_back_to_a_snippet_when_there_are_no_nouns():
    assert _label("그래서 그렇게 하기로 했어요").strip() != ""
