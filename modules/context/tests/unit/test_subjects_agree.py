"""The keyphrase check behind a link asserted on dense similarity -- no model, no database."""

from __future__ import annotations

from datetime import date

from autune_context.config import ContextSettings
from autune_context.pipeline.retrieval import Candidate
from autune_context.pipeline.topics import TopicSegment, content_nouns, keyphrases
from autune_context.service import _subjects_agree


class _KeywordEmbedder:
    """A vector per subject word, so keyphrase strings about the same subject meet."""

    model_version = "keyword-stub"
    _AXES = ("도크", "지게차", "피킹")

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(axis in t) for axis in self._AXES] + [0.01] for t in texts]


def _candidate(meeting_id: str, passage: str, similarity: float = 0.9) -> Candidate:
    return Candidate(
        linked_meeting_id=meeting_id,
        linked_meeting_date=date(2026, 9, 1),
        topic_label=meeting_id,
        similarity=similarity,
        fusion_score=0.1,
        passage=passage,
    )


_TOPIC = TopicSegment(
    label="도크 대기",
    text="도크 대기 시간이 줄었어요. 도크 예약제 효과가 있어요. 도크 배정은 자동이에요.",
    utterance_ids=["u1"],
    vector=[1.0],
)


def test_a_past_segment_about_the_same_subject_agrees_and_another_does_not() -> None:
    agree = _subjects_agree(
        _TOPIC,
        [
            _candidate("m_dock", "도크 앞에 트럭이 몰려요. 도크 예약을 받죠."),
            _candidate("m_forklift", "지게차 통로가 겹쳐요. 지게차 교육을 하죠."),
        ],
        _KeywordEmbedder(),
        ContextSettings(),
    )
    assert agree == {"m_dock": True, "m_forklift": False}


def test_every_past_segment_is_tried_not_only_the_passage() -> None:
    agree = _subjects_agree(
        _TOPIC,
        [_candidate("m1", "지게차 통로가 겹쳐요. 지게차 교육을 하죠.")],
        _KeywordEmbedder(),
        ContextSettings(),
        {"m1": ["지게차 통로가 겹쳐요.", "도크 앞에 트럭이 몰려요. 도크 예약을 받죠."]},
    )
    assert agree == {"m1": True}


def test_no_candidates_embeds_nothing() -> None:
    assert _subjects_agree(_TOPIC, [], _KeywordEmbedder(), ContextSettings()) == {}


def test_enough_shared_nouns_agree_even_when_the_keyphrases_do_not() -> None:
    topic = TopicSegment(
        label="정산", text="출장비 정산 영수증 마감 서류 제출", utterance_ids=["u1"], vector=[1.0]
    )
    agree = _subjects_agree(
        topic,
        [_candidate("m1", "출장비 정산 영수증 마감 서류 확인")],
        _KeywordEmbedder(),  # puts neither text on any subject axis
        ContextSettings(link_keyphrase_threshold=1.1),
    )
    assert agree == {"m1": True}


def test_keyphrases_leave_out_names_and_nest_nothing() -> None:
    text = "도윤 님, 재고 실사 일정은요? 재고 실사 일정이 밀렸어요. 도윤 견적도 봐요."
    phrases = keyphrases(text, frozenset({"도윤"}), k=3)
    assert phrases[0] == "재고 실사 일정"
    assert all("도윤" not in p for p in phrases)
    assert not any(a != b and a in b for a in phrases for b in phrases)
    assert "도윤" not in content_nouns(text, frozenset({"도윤"}))
