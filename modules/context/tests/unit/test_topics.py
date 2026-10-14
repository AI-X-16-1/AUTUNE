"""Topic segmentation and labelling — no model, no database."""

from __future__ import annotations

from autune_context.pipeline.topics import (
    _carries_no_topic,
    _label,
    extract_topics,
    person_names,
    topic_sentence,
)
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


def test_a_department_before_a_title_is_not_a_name():
    texts = [
        "개발 팀장님이 일정을 다시 보자고 하셨어요.",
        "영업 이사님께 견적을 보내요.",
        "마케팅 팀장님이 예산을 올렸어요.",
        "품질 부장님 승인이 필요해요.",
        "담당 매니저님이 확인했어요.",
    ]
    assert person_names(texts) == frozenset()


def test_a_common_noun_before_a_detached_honorific_or_a_seed_is_not_a_name():
    texts = ["초기 씨앗 자금을 모아요.", "수박 씨앗을 심어요.", "고객 님께 안내 문자를 보내요."]
    assert person_names(texts) == frozenset()


def test_a_department_still_labels_its_segment():
    text = (
        "개발 팀장님이 일정을 다시 보자고 하셨어요. 개발 일정이 두 주 밀렸어요."
        " 개발 일정은 다음 주에 다시 잡죠."
    )
    assert _label(text, names=person_names([text])) == "개발 일정"


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


def test_the_sentence_is_the_first_that_says_the_label_without_its_fillers():
    texts = [
        "지난주 일정부터 볼게요.",
        "음, 그러니까 결제 모듈 연동은 수요일까지 끝내야 해요. 테스트는 그다음이고요.",
        "결제 모듈 연동 테스트는 누가 맡죠?",
    ]
    assert topic_sentence("결제 모듈 연동", texts) == "결제 모듈 연동은 수요일까지 끝내야 해요."


def test_an_utterance_is_split_into_sentences_and_lines():
    texts = ["일정은 괜찮아요. 그런데 재고 실사는\n재고 실사 인원이 부족해요"]
    assert topic_sentence("재고 실사", texts) == "재고 실사는"
    assert topic_sentence("인원", ["일정은 괜찮아요! 인원이 부족해요?"]) == "인원이 부족해요?"


def test_no_sentence_when_none_says_the_label():
    assert topic_sentence("배포 일정", ["결제 모듈 연동은 끝났어요."]) is None
    assert topic_sentence("", ["결제 모듈 연동은 끝났어요."]) is None
    assert topic_sentence("배포 일정", []) is None


def test_a_long_sentence_is_cut_at_words_and_keeps_the_label():
    words = " ".join(f"단어{i}" for i in range(30))
    early = topic_sentence("결제 모듈", [f"결제 모듈은 {words}."], max_chars=40)
    assert early is not None
    assert early.startswith("결제 모듈은 ") and early.endswith("…") and len(early) <= 40
    late = topic_sentence("결제 모듈", [f"{words} 결제 모듈은 끝났어요 {words}."], max_chars=40)
    assert late is not None
    assert late.startswith("…결제 모듈은 끝났어요") and late.endswith("…") and len(late) <= 40


def test_the_label_of_a_segment_is_found_in_its_own_sentences():
    """``_label`` and ``topic_sentence`` read the same words: a label closed by a
    comma or a particle is still said as it stands."""
    texts = ["결제 모듈, 그거 확인했나요?", "결제 모듈 장애가 또 났어요."]
    label = _label(" ".join(texts))
    assert label == "결제 모듈"
    assert topic_sentence(label, texts) == "결제 모듈, 그거 확인했나요?"
