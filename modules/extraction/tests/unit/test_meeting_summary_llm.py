"""``summary_impl=llm`` (#421 v2): what is sent, what is kept, and when it is shown.

No network: a fake provider stands in for Gemini and records every body. The
rules under test: only consented lines are sent, the team's names never leave
and come back in the answer, a long meeting is summarised in sections inside
the outbound limit, a sentence the meeting does not support is dropped, a
summary of lines that changed since is not shown, deleted speech deletes it,
and a summary already written from the same lines is not asked for again.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from autune_core import Base, Meeting, Participant, TeamMember, User
from autune_core import Utterance as StoredUtterance
from autune_extraction import service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemRelated,
    ExtActionItemSource,
    ExtCalendarEvent,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionRef,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtEditEvent,
    ExtExternalRef,
    ExtMeetingNote,
    ExtMeetingSummary,
)
from autune_extraction.pipeline import summary as summary_module
from autune_extraction.pipeline.summary import LlmSummarizer, TooLongError, sections
from autune_integrations.privacy import MAX_OUTBOUND_CHARS

MEETING = "mtg_1"
TEAM = "team_1"


class Provider:
    """Answers like ``generateContent`` with whatever it was told to, in order."""

    def __init__(self, *answers: dict[str, Any]) -> None:
        self.answers = list(answers)
        self.bodies: list[dict[str, Any]] = []

    def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        self.bodies.append(json)
        answer = self.answers.pop(0) if self.answers else {}
        text = _dumps(answer)
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}

    def prompt(self, n: int) -> str:
        return str(self.bodies[n]["contents"][0]["parts"][0]["text"])

    @property
    def sent(self) -> str:
        return _dumps(self.bodies)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def summarizer(provider: Provider) -> LlmSummarizer:
    s = LlmSummarizer(api_key="never-in-a-body", model="first", base_url="http://llm.invalid")
    s._client = provider  # type: ignore[assignment]
    return s


LINES = [
    "박재경 님이 지난주 로그인 오류 원인을 찾았어요",
    "그럼 배포는 금요일로 미루죠",
    "제가 3시까지 릴리스 노트 정리할게요",
]
FINAL = {
    "overview": (
        "로그인 오류 원인을 공유했고 [사람1] 님 의견대로 배포를 금요일로 미루기로 했습니다."
    ),
    "points": ["배포는 금요일로 미룹니다", "릴리스 노트는 3시까지 정리합니다"],
}


# --- the summarizer ------------------------------------------------------------


def test_a_short_meeting_is_one_call_and_the_names_come_back() -> None:
    provider = Provider(FINAL)
    s = summarizer(provider)
    s.use_roster(["박재경"])

    written = s.summarize(LINES)

    assert written is not None
    assert len(provider.bodies) == 1
    assert "박재경" not in provider.sent and "재경" not in provider.sent
    assert "[사람1]" in provider.prompt(0)
    assert "never-in-a-body" not in provider.sent
    assert written.overview.startswith("로그인 오류 원인을 공유했고 박재경 님 의견대로")
    assert written.points == ("배포는 금요일로 미룹니다", "릴리스 노트는 3시까지 정리합니다")
    assert written.model_version == "llm:first"


def test_a_long_meeting_is_summarised_in_sections_inside_the_outbound_limit() -> None:
    line = "고객 인터뷰에서 나온 요청 사항을 하나씩 검토했고 박재경 님이 정리한 표를 같이 봤어요"
    lines = [f"{line} {n}번" for n in range(200)]
    parts = sections(lines)
    section_answer = {"points": ["[사람1] 님이 정리한 표를 검토했습니다"]}
    provider = Provider(*([section_answer] * len(parts)), FINAL)
    s = summarizer(provider)
    s.use_roster(["박재경"])

    written = s.summarize(lines)

    assert written is not None
    assert len(parts) > 1 and len(provider.bodies) == len(parts) + 1
    for body in provider.bodies:
        assert len(_dumps(body)) <= MAX_OUTBOUND_CHARS
    final = provider.prompt(len(parts))
    assert "부분별 요약" in final
    assert "[사람1] 님이 정리한 표" in final, "a placeholder stays one between the levels"
    assert "박재경" not in provider.sent


def test_a_point_the_meeting_does_not_support_is_dropped() -> None:
    provider = Provider(
        {
            "overview": FINAL["overview"],
            "points": [
                "배포는 금요일로 미룹니다",
                "예산 500만 원을 쓰기로 했습니다",  # a number nobody said
                "[사람7] 님이 맡습니다",  # a person never sent
                "두 줄\n짜리",
                42,
            ],
        }
    )
    s = summarizer(provider)
    s.use_roster(["박재경"])

    written = s.summarize(LINES)

    assert written is not None
    assert written.points == ("배포는 금요일로 미룹니다",)


def test_an_unusable_overview_means_no_summary() -> None:
    provider = Provider({"overview": "", "points": ["배포는 금요일로 미룹니다"]})

    assert summarizer(provider).summarize(LINES) is None


def test_nothing_to_summarise_sends_nothing() -> None:
    provider = Provider()

    assert summarizer(provider).summarize(["", "  "]) is None
    assert provider.bodies == []


def test_a_meeting_past_the_call_cap_gets_no_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(summary_module, "MAX_CALLS", 2)
    lines = ["긴 발화입니다 " * 40 for _ in range(30)]
    provider = Provider()

    with pytest.raises(TooLongError):
        summarizer(provider).summarize(lines)
    assert provider.bodies == [], "refused before the first call, not halfway"


def test_a_line_longer_than_a_call_is_left_out_not_sent() -> None:
    assert sections(["짧은 줄", "x" * MAX_OUTBOUND_CHARS, "또 짧은 줄"]) == [
        ["짧은 줄", "또 짧은 줄"]
    ]


# --- settings ------------------------------------------------------------------


def test_summary_impl_llm_needs_the_392_acknowledgement() -> None:
    with pytest.raises(ValueError, match="SUMMARY_IMPL=llm"):
        ExtractionSettings(_env_file=None, summary_impl="llm")  # type: ignore[call-arg]

    ExtractionSettings(  # type: ignore[call-arg]
        _env_file=None, summary_impl="llm", llm_acknowledged_392=True
    )


# --- stored, shown, forgotten ----------------------------------------------------

TABLES = [
    Meeting.__table__,
    User.__table__,
    TeamMember.__table__,
    Participant.__table__,
    StoredUtterance.__table__,
    ExtActionItem.__table__,
    ExtActionItemRelated.__table__,
    ExtActionItemSource.__table__,
    ExtCalendarEvent.__table__,
    ExtClassification.__table__,
    ExtConfirmation.__table__,
    ExtDecision.__table__,
    ExtDecisionRef.__table__,
    ExtDecisionReview.__table__,
    ExtDecisionSource.__table__,
    ExtEditEvent.__table__,
    ExtExternalRef.__table__,
    ExtMeetingNote.__table__,
    ExtMeetingSummary.__table__,
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as s:
        s.add(User(id="user_park", email="park@example.com", display_name="박재경"))
        s.add(TeamMember(team_id=TEAM, user_id="user_park"))
        s.add(
            Meeting(
                id=MEETING,
                team_id=TEAM,
                title="주간 회의",
                started_at=datetime(2026, 10, 1, 1, tzinfo=UTC),
            )
        )
        s.add(Participant(id="par_yes", meeting_id=MEETING, speaker_label="A", consented=True))
        s.add(Participant(id="par_no", meeting_id=MEETING, speaker_label="B", consented=False))
        rows = [(f"utt_{n}", "par_yes", text) for n, text in enumerate(LINES)]
        rows.append(("utt_secret", "par_no", "동의 안 한 사람의 말"))
        for n, (uid, who, text) in enumerate(rows):
            s.add(
                StoredUtterance(
                    id=uid,
                    meeting_id=MEETING,
                    participant_id=who,
                    speaker_label="화자",
                    start_sec=float(n),
                    end_sec=float(n) + 0.5,
                    text=text,
                )
            )
        s.flush()
        yield s


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: ExtractionSettings(_env_file=None),  # type: ignore[call-arg]
    )


def _store(session: Session) -> None:
    service.store_meeting_summary(
        session,
        MEETING,
        overview="배포를 금요일로 미루기로 했습니다.",
        points=["배포는 금요일", "릴리스 노트 정리"],
        model_version="llm:first",
        lines=service.summary_lines(session, MEETING),
    )


def test_only_consented_lines_are_what_a_summary_is_made_from(session: Session) -> None:
    assert service.summary_lines(session, MEETING) == LINES


def test_a_current_summary_is_on_the_tab(session: Session) -> None:
    _store(session)

    generated = service.meeting_summary(session, MEETING).generated

    assert generated is not None
    assert generated.overview == "배포를 금요일로 미루기로 했습니다."
    assert generated.points == ["배포는 금요일", "릴리스 노트 정리"]


def test_a_summary_of_lines_that_changed_since_is_not_shown(session: Session) -> None:
    _store(session)
    line = session.get(StoredUtterance, "utt_1")
    assert line is not None
    line.text = "그럼 배포는 다음 주로 미루죠"
    session.flush()

    assert service.meeting_summary(session, MEETING).generated is None


def test_deleted_speech_deletes_the_summary(session: Session) -> None:
    _store(session)

    service.forget_speech(session, ["utt_2"])

    assert session.get(ExtMeetingSummary, MEETING) is None


@pytest.fixture
def task_session(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    """``summarize_meeting`` opens its own sessions; hand it this one."""
    from contextlib import contextmanager  # noqa: PLC0415

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session

    monkeypatch.setattr(tasks, "session_scope", scope)
    return session


def test_the_task_stores_a_summary_and_does_not_ask_again_for_the_same_lines(
    task_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = Provider(FINAL)
    s = summarizer(provider)
    monkeypatch.setattr(tasks, "get_summarizer", lambda: s)

    assert tasks.summarize_meeting(MEETING) is True
    assert tasks.summarize_meeting(MEETING) is False

    assert len(provider.bodies) == 1
    assert "동의 안 한 사람의 말" not in provider.sent
    assert "박재경" not in provider.sent, "the team's roster is given to the summarizer"
    row = task_session.get(ExtMeetingSummary, MEETING)
    assert row is not None and row.overview.startswith("로그인 오류 원인을 공유했고 박재경")


def test_a_failed_call_leaves_the_tab_as_it_was(
    task_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Down(Provider):
        def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
            raise RuntimeError("provider down")

    s = summarizer(Down())
    monkeypatch.setattr(tasks, "get_summarizer", lambda: s)

    assert tasks.summarize_meeting(MEETING) is False
    assert task_session.get(ExtMeetingSummary, MEETING) is None


def test_with_no_summarizer_the_task_does_nothing(
    task_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "get_summarizer", lambda: None)

    assert tasks.summarize_meeting(MEETING) is False


# --- the prompt and the board (#421 v2, prompt revision) ---------------------------


def test_no_example_in_a_prompt_names_a_placeholder() -> None:
    """A ``[사람1]`` copied from an example would be put back as a real name."""
    for prompt in (summary_module._SECTION_PROMPT, summary_module._FINAL_PROMPT):
        assert re.search(r"\[사람\d", prompt) is None


def test_the_board_goes_to_the_last_call_only_with_its_names_replaced() -> None:
    line = "고객 인터뷰에서 나온 요청 사항을 하나씩 검토했고 정리한 표를 같이 봤어요"
    lines = [f"{line} {n}번" for n in range(200)]
    parts = sections(lines)
    provider = Provider(*([{"points": ["표를 검토했습니다"]}] * len(parts)), FINAL)
    s = summarizer(provider)
    s.use_roster(["박재경"])

    s.summarize(lines, board=["할 일(확인 전): 박재경 님이 표를 정리"])

    assert all("뽑아 둔 결정과 할 일" not in provider.prompt(n) for n in range(len(parts)))
    last = provider.prompt(len(parts))
    assert "- 할 일(확인 전): [사람1] 님이 표를 정리" in last
    assert "박재경" not in provider.sent
    for body in provider.bodies:
        assert len(_dumps(body)) <= MAX_OUTBOUND_CHARS


def test_a_number_the_board_carries_counts_as_said() -> None:
    provider = Provider(
        {"overview": "릴리스를 논의한 회의입니다.", "points": ["할 일: 7번 항목을 정리합니다"]}
    )

    written = summarizer(provider).summarize(["릴리스 얘기를 했어요"], board=["할 일: 7번 항목"])

    assert written is not None and written.points == ("할 일: 7번 항목을 정리합니다",)


def test_a_long_board_is_cut_to_its_share_of_the_last_call() -> None:
    provider = Provider(FINAL)
    board = [f"할 일(확인 전): 아주 긴 할 일 설명 {'가' * 80} {n}" for n in range(40)]

    summarizer(provider).summarize(LINES, board=board)

    assert len(_dumps(provider.bodies[0])) <= MAX_OUTBOUND_CHARS
    assert "할 일 설명" in provider.prompt(0)
    assert provider.prompt(0).count("- 할 일(확인 전)") < len(board)


def test_the_board_holds_only_what_a_model_wrote(session: Session) -> None:
    session.add_all(
        [
            ExtDecision(
                id="dec_ok",
                meeting_id=MEETING,
                statement="배포를 금요일로 미룬다",
                confidence=0.9,
                origin="model",
            ),
            ExtDecision(
                id="dec_no",
                meeting_id=MEETING,
                statement="거절된 결정",
                confidence=0.9,
                origin="model",
            ),
            ExtDecision(
                id="dec_typed",
                meeting_id=MEETING,
                statement="사람이 쓴 결정 010-1234-5678",
                confidence=1.0,
                origin="user",
            ),
            ExtActionItem(
                id="act_ok",
                meeting_id=MEETING,
                description="릴리스 노트 정리",
                status="todo",
                confidence=0.9,
                origin="model",
            ),
            ExtActionItem(
                id="act_typed",
                meeting_id=MEETING,
                description="사람이 쓴 할 일",
                status="todo",
                confidence=1.0,
                origin="user",
            ),
            ExtActionItem(
                id="act_edited",
                meeting_id=MEETING,
                description="사람이 고친 설명",
                status="needs_confirmation",
                confidence=0.9,
                origin="model",
            ),
        ]
    )
    session.add(
        ExtDecisionReview(
            decision_id="dec_no",
            meeting_id=MEETING,
            status="rejected",
            reviewed_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
    )
    session.add(
        ExtEditEvent(
            meeting_id=MEETING,
            action_item_id="act_edited",
            kind="edited",
            fields="description",
        )
    )
    session.flush()

    assert service.summary_board(session, MEETING) == [
        "결정(확인 전): 배포를 금요일로 미룬다",
        "할 일(확정): 릴리스 노트 정리",
    ]
