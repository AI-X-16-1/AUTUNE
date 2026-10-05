"""``summary_impl=llm`` (#421 v2): what is sent, what is kept, and when it is shown.

No network: a fake provider stands in for Gemini and records every body. The
rules under test: only consented lines are sent, the team's names never leave
and come back in the answer, a long meeting is summarised in sections inside
the outbound limit, a sentence the meeting does not support is dropped, a
summary of lines that changed since is not shown, deleted speech deletes it,
and a summary already written from the same lines is not asked for again.

From the review of #782: a stored summary whose lines changed is deleted, not
only hidden, however the next attempt ends; speech deleted while the model is
answering is not written back; and a line too long for a call is in no call.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from autune_contracts import TranscriptReady
from autune_contracts.transcript import PrivacyFlags, TranscriptMetadata, TranscriptSource
from autune_core import Base, Meeting, Participant, TeamMember, User
from autune_core import Utterance as StoredUtterance
from autune_extraction import service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.models import (
    ExtActionItem,
    ExtDecision,
    ExtDecisionReview,
    ExtEditEvent,
    ExtForgottenUtterance,
    ExtMeetingSummary,
)
from autune_extraction.pipeline import FakeClassifier, FakeNli
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


def test_a_line_too_long_for_a_call_is_not_in_the_only_call_either() -> None:
    """``sections`` left it out and the last prompt put it back (#782 review):
    a line just over a call's share went out whole from a one-call meeting."""
    too_long = "가" * summary_module._LINE_BUDGET
    provider = Provider(FINAL)
    s = summarizer(provider)
    s.use_roster(["박재경"])

    written = s.summarize([LINES[0], too_long, LINES[1], LINES[2]])

    assert written is not None
    assert len(provider.bodies) == 1
    assert "가" * 50 not in provider.sent
    assert LINES[1] in provider.prompt(0), "the lines around it are still summarised"


def test_a_long_line_with_a_board_keeps_the_last_call_inside_the_outbound_limit() -> None:
    """The board takes its share of the last call, so a line that fits a call
    alone may not fit beside it -- and then every summary of the meeting was
    refused by the outbound check (#782 review)."""
    board = [f"할 일(확인 전): 아주 긴 할 일 설명 {'나' * 80} {n}" for n in range(40)]
    provider = Provider(FINAL)
    s = summarizer(provider)
    s.use_roster(["박재경"])

    written = s.summarize([*LINES, "가" * 2500], board=board)

    assert written is not None
    assert len(_dumps(provider.bodies[0])) <= MAX_OUTBOUND_CHARS
    assert "가" * 50 not in provider.sent
    assert "할 일 설명" in provider.prompt(0)


def test_a_meeting_of_nothing_but_too_long_lines_sends_nothing() -> None:
    provider = Provider(FINAL)

    assert summarizer(provider).summarize(["가" * MAX_OUTBOUND_CHARS]) is None
    assert provider.bodies == []


# --- settings ------------------------------------------------------------------


def test_summary_impl_llm_needs_the_392_acknowledgement() -> None:
    with pytest.raises(ValueError, match="SUMMARY_IMPL=llm"):
        ExtractionSettings(_env_file=None, summary_impl="llm")  # type: ignore[call-arg]

    ExtractionSettings(  # type: ignore[call-arg]
        _env_file=None, summary_impl="llm", llm_acknowledged_392=True
    )


# --- stored, shown, forgotten ----------------------------------------------------

SHARED = {m.__tablename__ for m in (Meeting, User, TeamMember, Participant, StoredUtterance)}


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    # Every ``ext_`` table: a whole extraction runs against this below.
    tables = [
        t for name, t in Base.metadata.tables.items() if name in SHARED or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
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


# --- a stored summary follows the lines it was written from (#782 review) ----------


def _correct(session: Session) -> None:
    """A line changed after the summary was written, as a re-mask changes it."""
    line = session.get(StoredUtterance, "utt_1")
    assert line is not None
    line.text = "그럼 배포는 [날짜]로 미루죠"
    session.flush()


class Down(Provider):
    def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        raise RuntimeError("provider down")


def test_a_summary_of_changed_lines_is_deleted_even_when_the_call_fails(
    task_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not showing it is not removing it: the old text stayed in the table."""
    _store(task_session)
    _correct(task_session)
    s = summarizer(Down())
    monkeypatch.setattr(tasks, "get_summarizer", lambda: s)

    assert tasks.summarize_meeting(MEETING) is False
    assert task_session.get(ExtMeetingSummary, MEETING) is None


def test_when_no_consenting_line_is_left_the_summary_is_deleted_and_nothing_is_sent(
    task_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store(task_session)
    speaker = task_session.get(Participant, "par_yes")
    assert speaker is not None
    speaker.consented = False
    task_session.flush()
    provider = Provider(FINAL)
    s = summarizer(provider)
    monkeypatch.setattr(tasks, "get_summarizer", lambda: s)

    assert tasks.summarize_meeting(MEETING) is False
    assert task_session.get(ExtMeetingSummary, MEETING) is None
    assert provider.bodies == []


def test_with_no_summarizer_a_summary_of_changed_lines_still_goes(
    task_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``summary_impl`` set back to ``none`` must not keep what an earlier
    setting wrote from lines that have changed since."""
    monkeypatch.setattr(tasks, "get_summarizer", lambda: None)
    _store(task_session)

    assert tasks.summarize_meeting(MEETING) is False
    assert task_session.get(ExtMeetingSummary, MEETING) is not None, "current: it stays"

    _correct(task_session)

    assert tasks.summarize_meeting(MEETING) is False
    assert task_session.get(ExtMeetingSummary, MEETING) is None


def test_a_summarizer_switched_on_without_a_key_is_logged_not_raised(
    task_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_key() -> None:
        raise ValueError("AUTUNE_EXTRACTION_SUMMARY_IMPL=llm needs a key")

    monkeypatch.setattr(tasks, "get_summarizer", no_key)
    _store(task_session)
    _correct(task_session)

    assert tasks.summarize_meeting(MEETING) is False
    assert task_session.get(ExtMeetingSummary, MEETING) is None


def test_drop_stale_summary_keeps_a_current_one_and_deletes_a_stale_one(session: Session) -> None:
    _store(session)

    assert service.drop_stale_summary(session, MEETING) is False
    assert session.get(ExtMeetingSummary, MEETING) is not None

    _correct(session)

    assert service.drop_stale_summary(session, MEETING) is True
    assert session.get(ExtMeetingSummary, MEETING) is None


def test_deleted_speech_is_out_of_the_lines_before_its_utterance_is_gone(
    session: Session,
) -> None:
    """B's hook commits before A deletes the row; the line must not be read
    in between."""
    service.forget_speech(session, ["utt_2"])

    assert session.get(StoredUtterance, "utt_2") is not None, "A has not deleted it yet"
    assert session.get(ExtForgottenUtterance, "utt_2") is not None
    assert service.summary_lines(session, MEETING) == LINES[:2]

    service.forget_speech(session, ["utt_2"])  # safe to repeat


def test_speech_deleted_while_the_model_answers_is_not_written_back(
    task_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race of #782's review: the task read the lines, the model took its
    seconds, the speaker deleted a line meanwhile -- and the finished task
    stored a summary of it. The utterance is still in the table when the task
    comes to store, as it is between B's hook and A's deletion."""

    class DeletedMeanwhile(Provider):
        def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
            service.forget_speech(task_session, ["utt_2"])
            return super().request(method, path, json=json)

    provider = DeletedMeanwhile(FINAL)
    s = summarizer(provider)
    monkeypatch.setattr(tasks, "get_summarizer", lambda: s)

    assert tasks.summarize_meeting(MEETING) is False
    assert len(provider.bodies) == 1, "the model was asked and did answer"
    assert task_session.get(ExtMeetingSummary, MEETING) is None


def test_a_summary_asked_for_after_the_hook_does_not_send_the_deleted_line(
    task_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.forget_speech(task_session, ["utt_2"])
    provider = Provider(FINAL)
    s = summarizer(provider)
    monkeypatch.setattr(tasks, "get_summarizer", lambda: s)

    assert tasks.summarize_meeting(MEETING) is True
    assert "릴리스 노트 정리할게요" not in provider.sent
    assert LINES[1] in provider.prompt(0)


def test_a_line_corrected_while_the_model_answers_discards_the_answer(
    task_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    class CorrectedMeanwhile(Provider):
        def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
            _correct(task_session)
            return super().request(method, path, json=json)

    provider = CorrectedMeanwhile(FINAL)
    s = summarizer(provider)
    monkeypatch.setattr(tasks, "get_summarizer", lambda: s)

    assert tasks.summarize_meeting(MEETING) is False
    assert len(provider.bodies) == 1, "the model was asked and did answer"
    assert task_session.get(ExtMeetingSummary, MEETING) is None


# --- the extraction run itself (#782 review) ----------------------------------------


@pytest.fixture
def run(task_session: Session, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """``on_transcript_ready`` over the stored lines, fakes for the models;
    returns the meetings a summary was queued for."""
    queued: list[str] = []
    monkeypatch.setattr(tasks, "get_classifier", FakeClassifier)
    monkeypatch.setattr(tasks, "get_nli", FakeNli)
    monkeypatch.setattr(tasks, "publish", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "summarize_meeting", SimpleNamespace(delay=queued.append))
    for task in ("sync_item_copies", "sync_decision", "update_confirmation_dm"):
        monkeypatch.setattr(tasks, task, SimpleNamespace(delay=lambda ident: None))
    return queued


def _publish(session: Session) -> None:
    stored = service.stored_transcript(session, MEETING)
    tasks.on_transcript_ready(
        TranscriptReady(
            meeting_id=MEETING,
            utterances=stored,
            metadata=TranscriptMetadata(
                duration=4.0,
                source=next(iter(TranscriptSource)),
                privacy=PrivacyFlags(original_audio_deleted=True, pii_masked=True),
            ),
        ).model_dump(mode="json")
    )


def test_an_extraction_deletes_a_summary_of_changed_lines_with_the_summarizer_off(
    task_session: Session, run: list[str]
) -> None:
    """``summarize_meeting`` is not queued with ``summary_impl=none``, so the
    run itself has to drop what no longer matches."""
    _store(task_session)
    _publish(task_session)
    assert task_session.get(ExtMeetingSummary, MEETING) is not None, "same lines: it stays"

    _correct(task_session)
    _publish(task_session)

    assert task_session.get(ExtMeetingSummary, MEETING) is None
    assert run == []


def test_a_summarizer_without_a_key_does_not_fail_a_run_whose_rows_are_committed(
    task_session: Session, run: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_key() -> None:
        raise ValueError("AUTUNE_EXTRACTION_SUMMARY_IMPL=llm needs a key")

    monkeypatch.setattr(tasks, "get_summarizer", no_key)
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: ExtractionSettings(  # type: ignore[call-arg]
            _env_file=None, summary_impl="llm", llm_acknowledged_392=True
        ),
    )

    _publish(task_session)

    assert run == [MEETING], "queued by the setting; the task is where the key is missed"


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
