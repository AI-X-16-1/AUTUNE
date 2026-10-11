"""The live model's four calls, against a scripted GeminiText."""

from __future__ import annotations

from typing import Any

from autune_agent.live.model import (
    BUDGET,
    Detected,
    GeminiLive,
    Quote,
    Row,
    normalise,
)
from autune_agent.main.gemini import WebAnswer


class Scripted:
    """Stands in for GeminiText: answers in order, keeps what was sent."""

    def __init__(self, *answers: str, web: WebAnswer | None = None) -> None:
        self.answers = list(answers)
        self.sent: list[dict[str, Any]] = []
        self._web = web or WebAnswer(text="", sources=[])

    def generate(self, instructions: str, text: str, *, json_answer: bool) -> str:
        self.sent.append({"instructions": instructions, "text": text, "json": json_answer})
        return self.answers.pop(0)

    def search(self, instructions: str, question: str) -> WebAnswer:
        self.sent.append({"instructions": instructions, "text": question, "web": True})
        return self._web


ROWS = [
    Row(start=61.0, text="지난달에 가격 정책 뭐로 정했었지?"),
    Row(start=65.0, text="기억 안 나네"),
]


def test_detect_returns_at_most_two_new_questions() -> None:
    gemini = Scripted(
        '{"questions": [{"q": "지난달 가격 정책 결정", "web": false, "terms": ["가격 정책"]},'
        ' {"q": "경쟁사 가격", "web": true, "terms": ["경쟁사"]},'
        ' {"q": "셋째", "web": false, "terms": []}]}'
    )

    found = GeminiLive(gemini).detect(ROWS, known=[])

    assert found == [
        Detected(question="지난달 가격 정책 결정", web=False, terms=["가격 정책"]),
        Detected(question="경쟁사 가격", web=True, terms=["경쟁사"]),
    ]
    assert gemini.sent[0]["json"] is True
    assert "[01:01] 지난달에 가격 정책 뭐로 정했었지?" in gemini.sent[0]["text"]


def test_detect_drops_a_question_already_researched() -> None:
    gemini = Scripted('{"questions": [{"q": "  지난달  가격 정책 결정 ", "web": false}]}')

    assert GeminiLive(gemini).detect(ROWS, known=["지난달 가격 정책 결정"]) == []
    assert "지난달 가격 정책 결정" in gemini.sent[0]["text"]


def test_detect_reads_garbage_as_nothing() -> None:
    assert GeminiLive(Scripted("not json")).detect(ROWS, known=[]) == []
    assert GeminiLive(Scripted('{"questions": "x"}')).detect(ROWS, known=[]) == []


def test_detect_fits_the_budget_dropping_the_oldest_rows() -> None:
    rows = [Row(start=float(i), text="가" * 400) for i in range(12)]
    gemini = Scripted('{"questions": []}')

    GeminiLive(gemini).detect(rows, known=[])

    sent = gemini.sent[0]
    assert len(sent["instructions"]) + len(sent["text"]) <= BUDGET
    assert "[00:11]" in sent["text"] and "[00:00]" not in sent["text"]


def test_web_sends_the_question_alone() -> None:
    gemini = Scripted(web=WebAnswer(text="20달러", sources=[("p", "https://a.test")]))

    answer = GeminiLive(gemini).web("API 요금")

    assert answer.sources == [("p", "https://a.test")]
    assert gemini.sent[0]["text"] == "API 요금"


def test_write_sends_no_speaker_and_returns_the_text() -> None:
    gemini = Scripted("가격 정책\n- 지난달 회의에서 월 구독으로 정했습니다")

    body = GeminiLive(gemini).write(
        "지난달 가격 정책 결정",
        ROWS,
        [Quote(meeting_id="mtg_1", title="2026-09-10 가격 회의", body="월 구독으로 가죠")],
        WebAnswer(text="", sources=[]),
    )

    assert body.startswith("가격 정책")
    text = gemini.sent[0]["text"]
    assert "[2026-09-10 가격 회의] 월 구독으로 가죠" in text
    assert len(gemini.sent[0]["instructions"]) + len(text) <= BUDGET


def test_normalise_folds_space_and_case() -> None:
    assert normalise("  API   요금 ") == normalise("api 요금")


def test_ask_rewrites_a_pointed_line_as_one_question() -> None:
    text = Scripted('{"q": "Gemini API 요금은 얼마인가?", "terms": ["API 요금"]}')

    found = GeminiLive(text).ask("아 근데 그 API 요금 얼마였더라", [Row(start=1.0, text="앞 줄")])

    assert found == Detected(question="Gemini API 요금은 얼마인가?", web=True, terms=["API 요금"])
    assert "아 근데 그 API 요금 얼마였더라" in text.sent[0]["text"]
    assert "앞 줄" in text.sent[0]["text"]


def test_ask_finds_nothing_in_small_talk() -> None:
    assert GeminiLive(Scripted('{"q": ""}')).ask("네 좋아요", []) is None
    assert GeminiLive(Scripted("not json")).ask("네 좋아요", []) is None
