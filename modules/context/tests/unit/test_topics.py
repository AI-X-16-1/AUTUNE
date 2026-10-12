"""Topic segmentation and labelling — no model, no database."""

from __future__ import annotations

from autune_context.pipeline.topics import _carries_no_topic, _label, extract_topics, person_names
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


def test_when_nothing_repeats_the_phrase_that_opens_the_segment_names_it():
    # a real segment where every candidate was said once; the old tie-break
    # picked the first two-word phrase, 마무리 단계
    text = (
        "좋습니다. 민구님 디자인 쪽은 어떤가요? 거의 마무리 단계예요. "
        "다만 로그인 화면이 지금 네 장인데 세 장으로 줄이는 게 좋을 것 같습니다."
    )
    assert _label(text) == "디자인"


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


def test_a_name_written_apart_from_its_honorific_is_found():
    texts = [
        "도윤 님, 물류 쪽은 어떤가요?",
        "한서진 팀장님이 견적을 주셨어요.",
        "수빈 씨가 맡아요.",
    ]
    assert person_names(texts) == {"도윤", "한서진", "수빈"}


def test_a_customer_is_not_a_name():
    # 고객님 carries 님 but is no title: "신규" before it is not somebody's name
    assert person_names(["신규 고객님께 안내 문자를 보내요."]) == frozenset()


def test_a_name_never_labels_a_segment_even_where_it_comes_without_its_honorific():
    names = person_names(["도윤 님, 물류 쪽은 어떤가요?"])
    text = "도윤 님, 물류 쪽은 어떤가요? 도윤 견적은 금요일에 봐요. 도윤 의견도 듣고요."
    assert _label(text, names=names) == "물류"


def test_social_words_and_adverbial_nouns_never_label_a_segment():
    for text in (
        "그나마 다행이에요. 다행히 늦지 않았어요.",
        "수고 많으셨어요. 다들 수고하셨습니다.",
        "다음 시간에 이어서 하죠. 시간 맞춰 오세요.",
        "기본적으로 어려워요. 기본적으로는 다음 주예요.",
    ):
        assert _label(text) == "", text


def test_openings_and_closings_with_no_noun_are_left_out_before_the_split():
    utts = _utterances(
        ["자, 모였으면 시작합시다."]
        + ["AAA 결제 모듈 연동"] * 4
        + ["BBB 재고 실사 일정"] * 4
        + ["없으면 목요일에 다시 모이죠. 수고하셨습니다.", "네."]
    )
    segments = extract_topics(utts, _BlockEmbedder(), window=3, min_segment=3, depth_threshold=0.1)
    assert [s.label for s in segments] == ["AAA 결제 모듈 연동", "BBB 재고 실사 일정"]
    assert [s.utterance_ids for s in segments] == [
        [f"utt_{i}" for i in range(1, 5)],
        [f"utt_{i}" for i in range(5, 9)],
    ]


def test_a_sentence_with_no_noun_still_carries_its_topic_unless_it_is_social():
    for text in ("매월 5일까지 제출해야 한다.", "늦으면 다음 달로 넘어간다."):
        assert not _carries_no_topic(text, frozenset()), text
    for text in (
        "네.",
        "없습니다.",
        "자, 모였으면 시작합시다.",
        "수고 많으셨습니다, 다들.",
    ):
        assert _carries_no_topic(text, frozenset()), text
