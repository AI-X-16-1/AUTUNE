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
from structlog.testing import capture_logs

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
from autune_extraction.pipeline.summary import (
    LABELS,
    MAX_POINTS,
    LlmSummarizer,
    TooLongError,
    by_kind,
    names_someone,
    sections,
)
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
    "overview": ("로그인 오류 원인을 공유했고 배포를 금요일로 미루기로 했습니다."),
    "points": ["배포는 금요일로 미룹니다", "릴리스 노트는 3시까지 정리합니다"],
}


# --- the summarizer ------------------------------------------------------------


def test_a_short_meeting_is_one_call_and_no_name_leaves() -> None:
    provider = Provider(FINAL)
    s = summarizer(provider)
    s.use_roster(["박재경"])

    written = s.summarize(LINES)

    assert written is not None
    assert len(provider.bodies) == 1
    assert "박재경" not in provider.sent and "재경" not in provider.sent
    assert "[사람1]" in provider.prompt(0)
    assert "never-in-a-body" not in provider.sent
    assert written.overview == "로그인 오류 원인을 공유했고 배포를 금요일로 미루기로 했습니다."
    assert written.points == ("배포는 금요일로 미룹니다", "릴리스 노트는 3시까지 정리합니다")
    assert written.model_version == "llm:first"


def test_a_long_meeting_is_summarised_in_sections_inside_the_outbound_limit() -> None:
    line = "고객 인터뷰에서 나온 요청 사항을 하나씩 검토했고 박재경 님이 정리한 표를 같이 봤어요"
    lines = [f"{line} {n}번" for n in range(200)]
    # Cut as the summarizer cuts them: with the name already replaced.
    parts = sections([text.replace("박재경", "[사람1]") for text in lines])
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


def test_a_number_inside_a_longer_one_was_not_said() -> None:
    """ "20일" does not say 2: a date the answer shortened is a date nobody said."""
    lines = ["릴리스는 10월 20일에 내기로 했습니다", "점검은 09시에 시작하죠"]
    provider = Provider(
        {
            "overview": "릴리스 일정을 정한 회의입니다.",
            "points": [
                "결정: 릴리스는 10월 2일에 내기로 했습니다.",
                "결정: 릴리스는 10월 20일에 내기로 했습니다.",
                "결정: 릴리스는 1월 20일에 내기로 했습니다.",
                "논의: 점검은 9시에 시작합니다.",
            ],
        }
    )

    written = summarizer(provider).summarize(lines)

    assert written is not None
    assert written.points == (
        "결정: 릴리스는 10월 20일에 내기로 했습니다.",
        "논의: 점검은 9시에 시작합니다.",
    )


def test_an_overview_with_a_number_nobody_said_is_no_summary() -> None:
    provider = Provider(
        {"overview": "릴리스를 10월 2일에 내기로 한 회의입니다.", "points": ["배포를 논의했습니다"]}
    )

    assert summarizer(provider).summarize(["릴리스는 10월 20일에 내기로 했습니다"]) is None


def test_a_point_that_starts_with_a_quoted_phrase_keeps_both_marks() -> None:
    final = {
        "overview": '"처리 중입니다" 문구를 넣기로 한 회의입니다.',
        "points": ['결정: "처리 중입니다" 문구를 넣기로 했습니다.', '"따옴표로 감싼 문장입니다."'],
    }

    written = summarizer(Provider(final)).summarize(LINES)

    assert written is not None
    assert written.overview == '"처리 중입니다" 문구를 넣기로 한 회의입니다.'
    assert written.points == (
        '결정: "처리 중입니다" 문구를 넣기로 했습니다.',
        "따옴표로 감싼 문장입니다.",
    )


def test_an_unusable_overview_means_no_summary() -> None:
    provider = Provider({"overview": "", "points": ["배포는 금요일로 미룹니다"]})

    assert summarizer(provider).summarize(LINES) is None


# A meeting long enough for several section calls, with no name and no number
# a point below would have to match.
LONG = [
    f"고객 인터뷰에서 나온 요청 사항을 하나씩 검토했고 정리한 표를 같이 봤어요 {n}번"
    for n in range(200)
]
SECTION = {"points": ["정리한 표를 검토했습니다"]}


def test_a_point_in_a_list_of_its_own_is_read_as_the_point() -> None:
    # 2026-10-09, an invented meeting: a section answered with every point
    # wrapped, none was kept, and the summary was written from the other half.
    parts = sections(LONG)
    wrapped = {"points": [["정리한 표를 검토했습니다"], ["요청 사항을 하나씩 봤습니다"]]}
    final = {
        "overview": "요청 사항을 검토한 회의입니다.",
        "points": [["결정: 표를 다시 정리하기로 했습니다"], "할 일: 요청 사항을 나눠 봅니다"],
    }
    provider = Provider(*([wrapped] * len(parts)), final)

    written = summarizer(provider).summarize(LONG)

    assert written is not None
    last = provider.prompt(len(parts))
    assert "정리한 표를 검토했습니다" in last and "요청 사항을 하나씩 봤습니다" in last
    assert written.points == (
        "결정: 표를 다시 정리하기로 했습니다",
        "할 일: 요청 사항을 나눠 봅니다",
    )


def test_a_list_that_is_not_one_string_is_no_point() -> None:
    provider = Provider(
        {
            "overview": FINAL["overview"],
            "points": [
                ["배포는 금요일로 미룹니다", "릴리스 노트는 3시까지 정리합니다"],
                [],
                [42],
                [["배포는 금요일로 미룹니다"]],
                "릴리스 노트는 3시까지 정리합니다",
            ],
        }
    )

    written = summarizer(provider).summarize(LINES)

    assert written is not None
    assert written.points == ("릴리스 노트는 3시까지 정리합니다",)


def test_a_wrapped_point_that_names_someone_is_counted_like_any_other() -> None:
    assert summary_module._named({"overview": "", "points": [["제가 정리하겠습니다"]]}) == 1


@pytest.mark.parametrize(
    "nothing",
    [
        {},
        {"points": []},
        {"points": "정리한 표를 검토했습니다"},
        {"points": [42, ["정리한 표를", "검토했습니다"]]},
        {"points": ["예산 500만 원을 쓰기로 했습니다"]},  # a number nobody said
    ],
)
@pytest.mark.parametrize("failing", [0, 1])
def test_a_section_that_gives_no_point_means_no_summary_and_no_further_call(
    nothing: dict[str, Any], failing: int
) -> None:
    # Without it the last call is written from the other sections alone, and
    # the tab shows that as the meeting.
    parts = sections(LONG)
    assert len(parts) > failing + 1
    answers = [SECTION] * len(parts)
    answers[failing] = nothing
    provider = Provider(*answers, FINAL)

    with capture_logs() as logs:
        written = summarizer(provider).summarize(LONG)

    assert written is None
    assert len(provider.bodies) == failing + 1, "neither the next section nor the last call"
    (event,) = [e for e in logs if e["event"].startswith("extraction_summary")]
    assert {k: v for k, v in event.items() if k != "log_level"} == {
        "event": "extraction_summary_section_unusable",
        "calls": failing + 1,
    }


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
    assert row is not None and row.overview.startswith("로그인 오류 원인을 공유했고 배포를")


def test_an_answer_that_could_not_be_used_stores_nothing_and_the_tab_has_no_written_summary(
    task_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = summarizer(Provider({"overview": "", "points": ["배포는 금요일로 미룹니다"]}))
    monkeypatch.setattr(tasks, "get_summarizer", lambda: s)

    assert tasks.summarize_meeting(MEETING) is False
    assert task_session.get(ExtMeetingSummary, MEETING) is None
    assert service.meeting_summary(task_session, MEETING).generated is None


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
    """A ``[사람1]`` copied from an example would cost the sentence it is in."""
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


# --- the points kept, by kind (measured 2026-10-08) ---------------------------------

DECIDED = [f"결정: {what} 하기로 했습니다." for what in "가나다라마바사아자차카타"]
TASK = "할 일: 안내문 초안을 씁니다."
OPEN = ["남은 문제: 권한을 누가 줄지 정하지 못했습니다.", "남은 문제: 보상 기준이 남았습니다."]
TALKED = "논의: 자동 재시도를 두고 이야기했습니다."


def test_a_kind_the_model_wrote_last_is_not_cut_by_the_kind_it_wrote_first() -> None:
    """Twelve decisions, then a task, two open questions and a discussion: the
    first seven were seven decisions."""
    kept = by_kind([*DECIDED, TASK, *OPEN, TALKED])

    assert kept == [*DECIDED[:3], TASK, *OPEN, TALKED]


def test_every_kind_has_one_before_any_has_two() -> None:
    kept = by_kind([*DECIDED, TASK, *OPEN, TALKED], limit=4)

    assert kept == [DECIDED[0], TASK, OPEN[0], TALKED]


def test_points_inside_the_limit_are_all_kept_in_the_order_of_the_labels() -> None:
    """The order was broken once in five summaries: a task before a decision."""
    kept = by_kind([TASK, DECIDED[0], TALKED, OPEN[0], DECIDED[1]])

    assert kept == [DECIDED[0], DECIDED[1], TASK, OPEN[0], TALKED]


def test_points_with_no_label_are_the_first_of_them_as_before() -> None:
    """A section's answer: nothing to tell its points apart by."""
    plain = [f"{what} 이야기를 했습니다." for what in "가나다라마바사아자"]

    assert by_kind(plain) == plain[:MAX_POINTS]
    assert by_kind([*plain, TASK])[0] == TASK, "a labelled one is not behind nine plain ones"


def test_a_label_is_one_only_at_the_start_and_with_its_colon() -> None:
    odd = ["결정적인 이야기는 없었습니다.", "오늘 정한 것은 결정: 없음입니다."]

    assert by_kind([*odd, TASK]) == [TASK, *odd]


def test_no_more_than_the_limit_is_kept_whatever_the_kinds() -> None:
    assert len(by_kind([*DECIDED, TASK, *OPEN, TALKED])) == MAX_POINTS
    assert by_kind(DECIDED) == DECIDED[:MAX_POINTS]
    assert by_kind([]) == []


def test_the_summary_shows_an_open_question_the_model_wrote_after_ten_decisions() -> None:
    lines = [*LINES, *(p.split(": ", 1)[1] for p in [*DECIDED, TASK, *OPEN])]
    provider = Provider(
        {"overview": "여러 가지를 정한 회의입니다.", "points": [*DECIDED, TASK, *OPEN]}
    )

    written = summarizer(provider).summarize(lines)

    assert written is not None
    assert len(written.points) == MAX_POINTS
    assert [p.split(":")[0] for p in written.points] == ["결정"] * 4 + ["할 일"] + ["남은 문제"] * 2


def test_the_last_prompt_names_every_label_and_says_the_limit_is_a_limit() -> None:
    prompt = summary_module._FINAL_PROMPT.format(
        source="녹취록", lines="", board="", max_points=MAX_POINTS
    )

    for label in LABELS:
        assert f'"{label}: "' in prompt
    assert f"{MAX_POINTS}개를 넘기지 마세요" in prompt


# --- a meeting too long for a summary says so ---------------------------------------


def _lengthen(session: Session) -> None:
    """Enough consented speech for more sections than ``MAX_CALLS`` set to 2 allows."""
    for n in range(30):
        session.add(
            StoredUtterance(
                id=f"utt_long_{n:02d}",
                meeting_id=MEETING,
                participant_id="par_yes",
                speaker_label="화자",
                start_sec=100.0 + n,
                end_sec=100.5 + n,
                text="긴 발화입니다 " * 40,
            )
        )
    session.flush()


@pytest.fixture
def too_long(task_session: Session, monkeypatch: pytest.MonkeyPatch) -> Provider:
    """The meeting made too long, and a summarizer that would be asked about it."""
    _lengthen(task_session)
    monkeypatch.setattr(summary_module, "MAX_CALLS", 2)
    provider = Provider(FINAL)
    s = summarizer(provider)
    monkeypatch.setattr(tasks, "get_summarizer", lambda: s)
    return provider


def test_a_meeting_too_long_for_a_summary_is_marked_with_no_text(
    task_session: Session, too_long: Provider
) -> None:
    assert tasks.summarize_meeting(MEETING) is False

    row = task_session.get(ExtMeetingSummary, MEETING)
    assert row is not None
    assert row.too_long is True
    assert (row.overview, row.points) == ("", "")
    assert row.source_digest == service.source_digest(service.summary_lines(task_session, MEETING))
    assert too_long.bodies == []


def test_the_tab_is_told_a_meeting_was_too_long_and_is_given_no_summary(
    task_session: Session, too_long: Provider
) -> None:
    tasks.summarize_meeting(MEETING)

    shown = service.meeting_summary(task_session, MEETING)

    assert shown.generated is None
    assert shown.generated_too_long is True


def test_a_meeting_found_too_long_is_not_tried_again_for_the_same_lines(
    task_session: Session, too_long: Provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = tasks.get_summarizer()
    assert s is not None
    asked: list[int] = []
    summarize = s.summarize

    def counted(lines: Any, **kwargs: Any) -> Any:
        asked.append(len(lines))
        return summarize(lines, **kwargs)

    monkeypatch.setattr(s, "summarize", counted)

    assert tasks.summarize_meeting(MEETING) is False
    assert tasks.summarize_meeting(MEETING) is False

    assert len(asked) == 1


def test_too_long_is_logged_by_meeting_and_a_count_and_is_not_a_failure(
    task_session: Session, too_long: Provider
) -> None:
    with capture_logs() as logs:
        tasks.summarize_meeting(MEETING)

    events = [e for e in logs if e["event"].startswith("extraction_summary")]
    assert [e["event"] for e in events] == ["extraction_summary_too_long"]
    assert {k: v for k, v in events[0].items() if k not in ("event", "log_level")} == {
        "meeting_id": MEETING,
        "lines": len(LINES) + 30,
    }


def test_a_meeting_that_is_not_too_long_is_not_said_to_be(session: Session) -> None:
    assert service.meeting_summary(session, MEETING).generated_too_long is False
    _store(session)

    shown = service.meeting_summary(session, MEETING)

    assert shown.generated is not None
    assert shown.generated_too_long is False
    row = session.get(ExtMeetingSummary, MEETING)
    assert row is not None
    assert row.too_long is False


def _mark(session: Session) -> ExtMeetingSummary | None:
    return service.mark_summary_too_long(
        session,
        MEETING,
        model_version="llm:first",
        lines=service.summary_lines(session, MEETING),
    )


def test_too_long_is_said_only_of_the_lines_it_was_found_for(session: Session) -> None:
    assert _mark(session) is not None
    assert service.meeting_summary(session, MEETING).generated_too_long is True

    _correct(session)

    assert service.meeting_summary(session, MEETING).generated_too_long is False
    assert service.drop_stale_summary(session, MEETING) is True
    assert session.get(ExtMeetingSummary, MEETING) is None


def test_deleted_speech_deletes_the_too_long_row_as_it_does_a_summary(session: Session) -> None:
    _mark(session)

    service.forget_speech(session, ["utt_2"])

    assert session.get(ExtMeetingSummary, MEETING) is None


def test_too_long_is_not_recorded_for_lines_the_meeting_no_longer_has(session: Session) -> None:
    before = service.summary_lines(session, MEETING)
    _store(session)
    _correct(session)

    marked = service.mark_summary_too_long(
        session, MEETING, model_version="llm:first", lines=before
    )

    assert marked is None
    assert session.get(ExtMeetingSummary, MEETING) is None


def test_marking_too_long_leaves_none_of_an_earlier_summarys_text(session: Session) -> None:
    _store(session)

    _mark(session)

    row = session.get(ExtMeetingSummary, MEETING)
    assert row is not None
    assert (row.too_long, row.overview, row.points) == (True, "", "")
    assert service.meeting_summary(session, MEETING).generated is None


def test_a_summary_written_later_is_shown_and_no_longer_too_long(session: Session) -> None:
    _mark(session)

    _store(session)

    shown = service.meeting_summary(session, MEETING)
    assert shown.generated is not None
    assert shown.generated.overview == "배포를 금요일로 미루기로 했습니다."
    assert shown.generated_too_long is False


# --- the summary names no person (the user's choice, 2026-10-08) --------------------

NAMED = {
    "overview": (
        "배포 일정을 논의한 회의입니다. [사람1] 님 의견대로 배포를 금요일로 미루기로 했습니다. "
        "릴리스 노트는 정리하기로 했습니다."
    ),
    "points": [
        "결정: 배포는 금요일로 미루기로 했습니다.",
        "결정: [사람1] 님 의견대로 배포를 미루기로 했습니다.",
        "할 일: 릴리스 노트는 제가 정리하기로 했습니다.",
        "할 일: 릴리스 노트를 정리하기로 했습니다.",
        "남은 문제: 로그인 오류 문제가 다시 나는지는 모릅니다.",
    ],
}


def _named_summary() -> Any:
    s = summarizer(Provider(NAMED))
    s.use_roster(["박재경"])
    return s.summarize(LINES)


def test_a_point_that_names_someone_on_the_roster_is_not_shown() -> None:
    written = _named_summary()

    assert written is not None
    assert "결정: 배포는 금요일로 미루기로 했습니다." in written.points
    assert not any("사람1" in p or "박재경" in p for p in written.points)
    assert len([p for p in written.points if p.startswith("결정")]) == 1


def test_a_point_in_a_speakers_own_first_person_is_not_shown() -> None:
    """ "제가" on the tab would mean nobody -- or whoever is reading it."""
    written = _named_summary()

    assert written is not None
    assert [p for p in written.points if p.startswith("할 일")] == [
        "할 일: 릴리스 노트를 정리하기로 했습니다."
    ]


def test_a_word_that_only_ends_like_a_first_person_is_no_person() -> None:
    written = _named_summary()

    assert written is not None
    assert written.points[-1] == "남은 문제: 로그인 오류 문제가 다시 나는지는 모릅니다."


@pytest.mark.parametrize(
    ("sentence", "names"),
    [
        ("[사람1] 님이 맡기로 했습니다.", True),
        ("초안은 [사람12]에게 넘기기로 했습니다.", True),
        ("제가 보기로 했습니다.", True),
        ("그건 저는 반대입니다.", True),
        ("초안은 내가 쓰기로 했습니다.", True),
        ("문제가 남았습니다.", False),
        ("과제가 많다는 이야기가 있었습니다.", False),
        ("안내가 늦었다는 지적이 있었습니다.", False),
        ("[날짜]까지 하기로 했습니다.", False),
        ("초안은 금요일까지 쓰기로 했습니다.", False),
    ],
)
def test_what_counts_as_naming_someone(sentence: str, names: bool) -> None:
    assert names_someone(sentence) is names


def test_an_overview_loses_only_its_sentence_that_names_someone() -> None:
    written = _named_summary()

    assert written is not None
    assert written.overview == ("배포 일정을 논의한 회의입니다. 릴리스 노트는 정리하기로 했습니다.")


def test_an_overview_that_only_names_people_means_no_summary() -> None:
    answer = {
        "overview": "[사람1] 님 의견대로 배포를 미루기로 했습니다.",
        "points": NAMED["points"],
    }
    s = summarizer(Provider(answer))
    s.use_roster(["박재경"])

    assert s.summarize(LINES) is None


def test_no_name_of_the_roster_is_put_back_anywhere() -> None:
    written = _named_summary()

    assert written is not None
    assert "박재경" not in written.overview + "".join(written.points)
    assert "[사람" not in written.overview + "".join(written.points)


def test_a_name_that_is_not_on_the_roster_passes_the_check() -> None:
    """The limit the check has, said where it was decided: such a name was
    never replaced, so nothing marks it."""
    answer = {
        "overview": FINAL["overview"],
        "points": ["할 일: 릴리스 노트는 외부의 홍길동 님이 정리하기로 했습니다."],
    }
    s = summarizer(Provider(answer))
    s.use_roster(["박재경"])

    written = s.summarize([*LINES, "릴리스 노트는 홍길동 님이 정리하기로 했어요"])

    assert written is not None
    assert written.points == ("할 일: 릴리스 노트는 외부의 홍길동 님이 정리하기로 했습니다.",)


def test_both_prompts_ask_for_no_person_and_show_none_in_their_examples() -> None:
    for prompt in (summary_module._SECTION_PROMPT, summary_module._FINAL_PROMPT):
        assert "사람을 쓰지 마세요" in prompt
        example = prompt.split("예시(다른 회의의 답):")[1]
        assert "담당" not in example
        assert not names_someone(example)


def test_how_many_sentences_named_someone_is_logged_as_a_count_only() -> None:
    with capture_logs() as logs:
        _named_summary()

    (event,) = [e for e in logs if e["event"] == "extraction_summary_written"]
    assert {k: v for k, v in event.items() if k not in ("event", "log_level")} == {
        "calls": 1,
        "lines": len(LINES),
        "named": 3,
    }
