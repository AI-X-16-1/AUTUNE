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


def test_a_phrase_said_twice_beats_one_of_its_words_said_three_times():
    # #352: every link from this meeting sat at 0.50 under a one-word label
    text = (
        "네 그럼 결제부터 볼까요. 결제 모듈 연동은 카드사 쪽 답변이 아직 안 왔어요. "
        "그럼 결제 모듈 연동 일정은 다음 주 수요일로 확정할게요."
    )
    assert _label(text) == "결제 모듈 연동"


def test_a_word_the_dictionary_lacks_is_labelled_whole():
    # kiwipiepy splits 온보딩 into 온/MM 보/NNG 딩/MAG; the old label was "보"
    assert _label("온보딩 화면은 지금 몇 장이에요? 온보딩 문구 정리해서 드릴게요.") == "온보딩"


def test_ties_go_to_the_longer_phrase_then_the_one_said_first():
    text = (
        "이벤트 예산이 지난번보다 늘었던데요. 쿠폰 이벤트 비용이 2배로 잡혀서 그래요. "
        "쿠폰 이벤트는 예산 안에서만 하죠. 이벤트 예산은 다음 회의 때 다시 확인해요."
    )
    assert _label(text) == "이벤트 예산"


def test_numbers_counters_names_and_meeting_talk_never_label_a_segment():
    for text in (
        "2번이요 2번. 10분 뒤에 다시 할까요? 오늘은 2시까지만 하죠.",
        "민재님 민재님 민재님 확인 부탁드려요.",
        "김** 김** 김**님이 010-****-1234 로 부탁드려요.",  # module A's masking
        "다음 회의 때 다시 얘기해요. 오늘 회의는 여기까지.",
        "그래서 그렇게 하기로 했어요",
    ):
        assert _label(text) == "", text


def test_a_latin_name_with_digits_is_a_noun():
    assert _label("v2.0 출시 킥오프. v2.0 출시 일정.") == "v2.0 출시"


def test_a_segment_with_nothing_to_name_it_is_not_a_topic():
    utts = _utterances(["AAA 결제 모듈 연동"] * 5 + ["음 네 네 맞아요"] * 5)
    segments = extract_topics(utts, _BlockEmbedder(), window=3, min_segment=3, depth_threshold=0.1)
    assert [s.label for s in segments] == ["AAA 결제 모듈 연동"]
    assert segments[0].utterance_ids == [f"utt_{i}" for i in range(5)]
