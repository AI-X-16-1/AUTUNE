"""The two LLM calls Research makes, against a fake Gemini."""

from __future__ import annotations

import pytest

from autune_agent.subagents.research.writer import (
    MAX_TERMS,
    TERMS_INSTRUCTIONS,
    WRITE_INSTRUCTIONS,
    GeminiWriter,
    Match,
    WriterError,
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


def test_write_stays_within_outbound_budget() -> None:
    """Verify that assembled write text + instructions stays within budget."""
    fake = FakeText("## 제기된 질문\n제기된 것들")
    questions = ["배포는 언제죠?", "QA는 누가 하죠?", "예산은 얼마죠?"]
    matches = [
        Match(
            utterance_id=f"utt_{n}",
            meeting_id="mtg_1",
            title="2026-09-23 리뷰 · 00:03",
            body="가" * 800,
        )
        for n in range(6)
    ]

    long_title = "매우_긴_회의_이름_여기에_길게_작성됨" * 3
    GeminiWriter(text=fake).write(  # type: ignore[arg-type]
        meeting_title=long_title, questions=questions, matches=matches
    )

    # Verify the sent text + instructions fits within budget
    sent_text = fake.sent[0]
    total_len = len(WRITE_INSTRUCTIONS) + len(sent_text)
    msg = f"Total {total_len} exceeds budget {MAX_OUTBOUND_CHARS}"
    assert total_len <= MAX_OUTBOUND_CHARS, msg

    # Verify highest-ranked matches are kept (prefix of input order)
    for i in range(len(matches)):
        match_id = f"utt_{i}"
        if match_id in sent_text:
            # All earlier matches should also be present
            for j in range(i):
                utt_j = f"utt_{j}"
                utt_i = f"utt_{i}"
                msg_m = f"Lower-ranked match {utt_j} missing but {utt_i} present"
                assert utt_j in sent_text, msg_m


def test_terms_stays_within_outbound_budget() -> None:
    """Verify that assembled terms text + instructions stays within budget."""
    fake = FakeText('{"terms": ["배포", "QA"]}')
    questions = ["배포는 언제죠?"] * 100  # Many questions

    GeminiWriter(text=fake).terms(questions)  # type: ignore[arg-type]

    # Verify the sent text + instructions fits within budget
    sent_text = fake.sent[0]
    total_len = len(TERMS_INSTRUCTIONS) + len(sent_text)
    assert total_len <= MAX_OUTBOUND_CHARS, f"Total {total_len} exceeds budget {MAX_OUTBOUND_CHARS}"


def test_write_with_very_long_title() -> None:
    """Write with 5000-char title must truncate and stay within budget."""
    fake = FakeText("## 제기된 질문\nResult")
    long_title = "가" * 5000

    GeminiWriter(text=fake).write(  # type: ignore[arg-type]
        meeting_title=long_title, questions=["q"], matches=[]
    )

    # Verify the sent text + instructions fits within budget
    sent_text = fake.sent[0]
    total_len = len(WRITE_INSTRUCTIONS) + len(sent_text)
    msg = f"Total {total_len} exceeds budget {MAX_OUTBOUND_CHARS}"
    assert total_len <= MAX_OUTBOUND_CHARS, msg

    # Title should be truncated (not the full 5000 chars)
    assert len(sent_text) < len(long_title)


def test_write_drops_questions_when_they_alone_exceed_limit() -> None:
    """Write with many long questions must drop trailing ones."""
    fake = FakeText("## 제기된 질문\nResult")
    # 20 questions of 400 chars each = 8000 chars (alone exceeds budget)
    long_q = "가" * 400
    questions = [long_q] * 20

    GeminiWriter(text=fake).write(  # type: ignore[arg-type]
        meeting_title="회의", questions=questions, matches=[]
    )

    # Verify the sent text + instructions fits within budget
    sent_text = fake.sent[0]
    total_len = len(WRITE_INSTRUCTIONS) + len(sent_text)
    msg = f"Total {total_len} exceeds budget {MAX_OUTBOUND_CHARS}"
    assert total_len <= MAX_OUTBOUND_CHARS, msg

    # Should keep only a prefix of the questions
    kept_count = sent_text.count("- " + long_q[:20])
    assert kept_count < 20, "Not all questions were kept; some were dropped"

    # Verify kept questions are a prefix (no gaps)
    q_starts = [sent_text.find(f"- {long_q}")]
    if q_starts[0] >= 0:
        # Count how many times the question appears consecutively
        count = 0
        pos = sent_text.find("질문:\n") + len("질문:\n")
        while pos < len(sent_text):
            if sent_text[pos : pos + 2] == "- ":
                count += 1
                pos = sent_text.find("\n", pos) + 1
            else:
                break


def test_terms_drops_questions_when_they_alone_exceed_limit() -> None:
    """Terms with many long questions must drop trailing ones."""
    fake = FakeText('{"terms": ["배포"]}')
    # 20 questions of 400 chars each = 8000 chars (alone exceeds budget)
    long_q = "가" * 400
    questions = [long_q] * 20

    GeminiWriter(text=fake).terms(questions)  # type: ignore[arg-type]

    # Verify the sent text + instructions fits within budget
    sent_text = fake.sent[0]
    total_len = len(TERMS_INSTRUCTIONS) + len(sent_text)
    msg = f"Total {total_len} exceeds budget {MAX_OUTBOUND_CHARS}"
    assert total_len <= MAX_OUTBOUND_CHARS, msg

    # Should keep only a prefix of the questions (not all 20)
    kept_count = sent_text.count("- " + long_q[:20])
    assert kept_count < 20, "Not all questions were kept; some were dropped"
