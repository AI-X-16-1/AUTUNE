"""The two LLM calls Research makes, against a fake Gemini."""

from __future__ import annotations

import pytest

from autune_agent.subagents.research.writer import (
    MAX_TERMS,
    GeminiWriter,
    Match,
    WriterError,
    fit,
)
from autune_integrations.privacy import MAX_OUTBOUND_CHARS


class FakeText:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.sent: list[str] = []

    def generate(self, instructions: str, text: str, *, json_answer: bool) -> str:
        self.sent.append(text)
        return self.answer


def test_terms_are_parsed_and_capped() -> None:
    fake = FakeText('{"terms": ["배포", "QA", "예산", "일정", "리뷰", "인력", "보안"]}')

    terms = GeminiWriter(text=fake).terms(["배포는 언제죠?"])  # type: ignore[arg-type]

    assert terms == ["배포", "QA", "예산", "일정", "리뷰"][:MAX_TERMS]


@pytest.mark.parametrize("answer", ["음...", '{"terms": "배포"}', '{"x": []}', '{"terms": [1, 2]}'])
def test_an_answer_that_is_not_a_list_of_terms_means_no_terms(answer: str) -> None:
    assert GeminiWriter(text=FakeText(answer)).terms(["q"]) == []  # type: ignore[arg-type]


def test_terms_longer_than_a_query_are_dropped() -> None:
    fake = FakeText('{"terms": ["배포", "' + "가" * 101 + '"]}')

    assert GeminiWriter(text=fake).terms(["q"]) == ["배포"]  # type: ignore[arg-type]


def test_write_sends_questions_and_matches_and_returns_the_text() -> None:
    fake = FakeText("## 제기된 질문\n- 배포 일정")
    match = Match(utterance_id="utt_1", meeting_id="mtg_1", title="9/23 리뷰", body="금요일 배포")

    body = GeminiWriter(text=fake).write(  # type: ignore[arg-type]
        meeting_title="스프린트", questions=["배포는 언제죠?"], matches=[match]
    )

    assert body.startswith("## 제기된 질문")
    assert "금요일 배포" in fake.sent[0] and "배포는 언제죠?" in fake.sent[0]


def test_an_empty_document_is_a_writer_error() -> None:
    with pytest.raises(WriterError):
        GeminiWriter(text=FakeText("  ")).write(  # type: ignore[arg-type]
            meeting_title="t", questions=["q"], matches=[]
        )


def test_fit_drops_the_lowest_ranked_matches_first() -> None:
    questions = ["질문"]
    matches = [
        Match(utterance_id=f"utt_{n}", meeting_id="mtg_1", title="t", body="가" * 900)
        for n in range(6)
    ]

    kept_q, kept_m = fit(questions, matches, limit=MAX_OUTBOUND_CHARS)

    assert kept_q == questions
    assert [m.utterance_id for m in kept_m] == [m.utterance_id for m in matches[: len(kept_m)]]
    assert sum(len(m.body) + len(m.title) for m in kept_m) + len("질문") <= MAX_OUTBOUND_CHARS
