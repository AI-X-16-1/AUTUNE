"""A short title beside an item's and a decision's sentence (module B's owner, 2026-10-09).

No network: a provider stands in for the model and answers what it is told to.
The rules under test: a title is kept only when it is twenty characters or
fewer, ends in a noun and is made of the sentence's own words -- with no date
and no person on an item; a row without an accepted title has none; a
sentence a person wrote is never titled; another sentence in the row takes
the title away; and nothing of the text is in a log line.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_core import (
    Base,
    Meeting,
    Participant,
    PrivacyViolationError,
    Team,
    TeamMember,
    User,
    Utterance,
)
from autune_extraction import service, tasks
from autune_extraction.config import ExtractionSettings
from autune_extraction.decisions import core_of
from autune_extraction.models import (
    ExtActionItem,
    ExtDecision,
    ExtDecisionReview,
    ExtEditEvent,
)
from autune_extraction.pipeline import title as title_module
from autune_extraction.pipeline.title import TITLE_MAX, LlmTitler, Title, TitleRequest, accept
from autune_extraction.schemas import ActionItemUpdate, DecisionReviewUpdate
from autune_integrations.privacy import check_outbound

MEETING = "mtg_1"
TEAM = "team_1"

LOGS = "다음 주 화요일까지 결제 화면 오류 로그를 모아서 정리 예정"
REPORT = "금요일까지 보고서 정리 예정"
POSTPONED = "배포는 다음 주 금요일로 미루기로 함"


# -- the rules ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "text", "kind"),
    [
        ("결제 화면 오류 로그 정리", LOGS, "item"),
        ("보고서 정리", REPORT, "item"),
        ("배포 다음 주 금요일로 연기", POSTPONED, "decision"),
        # An item may be to decide something; a noun that ends in 함 is a noun.
        ("요금제 결정", "요금제는 제가 정해서 공유 예정", "item"),
        ("부가세 포함", "견적서는 부가세 포함해서 다시 작성 예정", "item"),
        # How long is not when.
        ("교육 2시간 진행", "신입 교육을 2시간 진행 예정", "item"),
        # Quotation marks and a full stop a model adds are not part of a title.
        ('"보고서 정리."', REPORT, "item"),
        # Several things, pointing at none; and the end of another word.
        ("이것저것 정리", "이것저것 정리 예정", "item"),
        ("시안이거나 초안 검토", "시안이거나 초안을 검토 예정", "item"),
        # A name that starts as "이건" does is a word of its own.
        ("이건우 대리 자료 검토", "이건우 대리 자료를 검토 예정", "item"),
    ],
)
def test_a_title_that_keeps_every_rule_is_accepted(answer: str, text: str, kind: str) -> None:
    title, reason = accept(answer, TitleRequest(text, kind))  # type: ignore[arg-type]

    assert reason == ""
    assert title is not None and len(title) <= TITLE_MAX
    assert title == answer.strip('".')


@pytest.mark.parametrize(
    ("answer", "text", "kind", "reason"),
    [
        ("", REPORT, "item", "empty"),
        ("보고서\n정리", REPORT, "item", "more than one line"),
        ("결제 화면에서 나는 오류 로그 전부 정리", LOGS, "item", "over 20 characters"),
        ("보고서 정리 (금요일)", REPORT, "item", "a bracket or a cut mark"),
        ("결제 화면 오류…", LOGS, "item", "a bracket or a cut mark"),
        ("보고서를 정리함", REPORT, "item", "does not end in a noun"),
        ("배포 연기됨", POSTPONED, "decision", "does not end in a noun"),
        ("보고서 정리 예정", REPORT, "item", "does not end in a noun"),
        ("보고서를 정리합니다", REPORT, "item", "does not end in a noun"),
        ("보고서 정리해요", REPORT, "item", "does not end in a noun"),
        ("배포 미루기로", POSTPONED, "decision", "does not end in a noun"),
        ("A안으로 결정", "A안으로 진행", "decision", "does not end in a noun"),
        (
            "서버 3대 증설",
            "서버를 2대 늘리기로 함",
            "decision",
            "a number the sentence does not say",
        ),
        ("민경 님 로그 정리", "민경 님이 로그 정리 예정", "item", "names a person"),
        ("금요일까지 보고서 정리", REPORT, "item", "a date on an item"),
        ("배포 내일로 연기", POSTPONED, "decision", "a date the sentence does not say"),
        ("마케팅 예산 전면 재검토", POSTPONED, "decision", "words the sentence does not have"),
        # A word that only points, alone or beside a noun: its words are the
        # sentence's own, so no other rule refuses it.
        ("이거", "이거를 하겠다고 약속함", "item", "a pointing word"),
        ("이거 진행", "이거를 진행하겠다고 약속함", "item", "a pointing word"),
        ("그거 확인", "그거는 제가 확인 예정", "item", "a pointing word"),
        ("저거 수정", "저거를 수정 예정", "item", "a pointing word"),
        ("이것 검토", "이것을 검토 예정", "item", "a pointing word"),
        ("그것으로 변경", "그것으로 변경하기로 함", "decision", "a pointing word"),
        ("저것 삭제", "저것은 삭제하기로 함", "decision", "a pointing word"),
        ("이건 확인", "이건 제가 확인 예정", "item", "a pointing word"),
        ("그건 보류", "그건 보류하기로 함", "decision", "a pointing word"),
        ("저건 수정", "저건 수정 예정", "item", "a pointing word"),
        ("이걸 검토", "이걸 검토 예정", "item", "a pointing word"),
        ("그걸로 진행", "그걸로 진행하기로 함", "decision", "a pointing word"),
        ("저걸 정리", "저걸 정리 예정", "item", "a pointing word"),
        ("이게 문제", "이게 문제라서 수정 예정", "item", "a pointing word"),
        ("그게 원인", "그게 원인이라 수정 예정", "item", "a pointing word"),
        ("저게 원인", "저게 원인이라 수정 예정", "item", "a pointing word"),
    ],
)
def test_a_title_that_breaks_a_rule_is_refused_with_the_rule(
    answer: str, text: str, kind: str, reason: str
) -> None:
    assert accept(answer, TitleRequest(text, kind), names=("민경",)) == (None, reason)  # type: ignore[arg-type]


def test_twenty_characters_is_the_most_and_spaces_count() -> None:
    text = "가나다라 마바사아 자차카타 파하가나 다라마바 사아자차"
    twenty = "가나다라 마바사아 자차카타 파하가나"
    assert len(twenty) == TITLE_MAX - 1

    assert accept(twenty + "다", TitleRequest(text))[0] == twenty + "다"
    assert accept(twenty + " 다라", TitleRequest(text)) == (None, "over 20 characters")


def test_a_refusal_names_the_rule_and_nothing_of_the_text() -> None:
    _, reason = accept("민경 님 연락처 010-1234-5678", TitleRequest("x"), names=("민경",))

    assert reason and "민경" not in reason and "010" not in reason


def test_a_decisions_bracket_is_not_part_of_what_is_titled() -> None:
    assert core_of("A안으로 진행 (담당 민경, 기한 2026-10-13)") == "A안으로 진행"
    assert core_of("A안으로 진행 (담당 민경)") == "A안으로 진행"
    assert core_of("A안으로 진행 (기한 다음 주)") == "A안으로 진행"
    assert core_of("A안으로 진행") == "A안으로 진행"
    assert core_of("표 (초안) 확정") == "표 (초안) 확정", "brackets of another kind stay"


# -- the model ----------------------------------------------------------------


class Provider:
    """Answers like ``generateContent`` with what it was told to, in order,
    after checking the body the way ``HttpClient.request`` does."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.bodies: list[dict[str, Any]] = []

    def request(self, method: str, path: str, *, json: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
        check_outbound(json, destination="llm")
        self.bodies.append(json)
        answer = self.answers.pop(0) if self.answers else {}
        text = answer if isinstance(answer, str) else _dumps(answer)
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}

    def prompt(self, n: int) -> str:
        return str(self.bodies[n]["contents"][0]["parts"][0]["text"])


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def titler(provider: Provider, roster: tuple[str, ...] = ()) -> LlmTitler:
    made = LlmTitler(api_key="never-in-a-body", model="first", base_url="http://llm.invalid")
    made._client = provider  # type: ignore[assignment]
    made.use_roster(roster)
    return made


def answered(*titles: str | None) -> dict[str, Any]:
    return {
        "titles": [{"n": n, "title": t} for n, t in enumerate(titles, 1) if t is not None],
    }


def test_each_row_gets_the_title_written_for_it() -> None:
    provider = Provider(answered("결제 화면 오류 로그 정리", "배포 다음 주 금요일로 연기"))

    titles = titler(provider).titles(
        [TitleRequest(LOGS, "item"), TitleRequest(POSTPONED, "decision")]
    )

    assert [t.text for t in titles] == ["결제 화면 오류 로그 정리", "배포 다음 주 금요일로 연기"]
    assert len(provider.bodies) == 1, "a meeting is one call"
    assert f"1. [할 일] {LOGS}" in provider.prompt(0)
    assert f"2. [결정] {POSTPONED}" in provider.prompt(0)


def test_a_refused_or_missing_title_leaves_that_row_without_one() -> None:
    provider = Provider(answered("보고서를 정리함", None, "배포 다음 주 금요일로 연기"))

    titles = titler(provider).titles(
        [TitleRequest(REPORT), TitleRequest(LOGS), TitleRequest(POSTPONED, "decision")]
    )

    assert titles[0] == Title(None, "does not end in a noun", "보고서를 정리함")
    assert titles[1] == Title(None, "no answer")
    assert titles[2].text == "배포 다음 주 금요일로 연기"
    assert len(provider.bodies) == 1, "a refused title is not asked for again"


@pytest.mark.parametrize(
    "answer", ["not json", "[]", {"titles": "none"}, {"titles": [3, {"n": "1"}]}]
)
def test_an_answer_that_cannot_be_read_titles_nothing(answer: Any) -> None:
    titles = titler(Provider(answer)).titles([TitleRequest(REPORT)])

    assert titles == [Title(None, "no answer")]


def test_an_answer_for_a_row_that_was_not_asked_about_is_not_used() -> None:
    provider = Provider(
        {
            "titles": [
                {"n": 2, "title": "보고서 정리"},
                {"n": 1, "title": "보고서 정리"},
                {"n": 1, "title": "x"},
            ]
        }
    )

    titles = titler(provider).titles([TitleRequest(REPORT)])

    assert [t.text for t in titles] == ["보고서 정리"]


def test_the_teams_names_do_not_leave_and_do_not_come_back_in_a_title() -> None:
    said = "김민경 님이 결제 화면 오류 로그 정리 예정"
    provider = Provider(answered("[사람1] 오류 로그 정리"), answered("김민경 오류 로그 정리"))
    asked = titler(provider, roster=("김민경",))

    first = asked.titles([TitleRequest(said)])
    second = asked.titles([TitleRequest(said)])

    assert "김민경" not in _dumps(provider.bodies) and "[사람1]" in provider.prompt(0)
    assert first[0].text is None and first[0].reason == "a bracket or a cut mark"
    assert second[0].text is None and second[0].reason == "names a person"


def test_many_rows_are_asked_about_in_calls_of_a_few(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(title_module, "MAX_PER_CALL", 2)
    provider = Provider(answered("보고서 정리", "보고서 정리"), answered("보고서 정리"))

    titles = titler(provider).titles([TitleRequest(REPORT)] * 3)

    assert [t.text for t in titles] == ["보고서 정리"] * 3
    assert len(provider.bodies) == 2
    assert "3." not in provider.prompt(0) and "1. [할 일]" in provider.prompt(1)


def test_nothing_is_asked_about_no_rows() -> None:
    provider = Provider()

    assert titler(provider).titles([]) == []
    assert titler(provider).titles([TitleRequest("  ")]) == [Title(None, "not asked")]
    assert provider.bodies == []


def test_a_sentence_the_outbound_check_refuses_is_raised_and_nothing_leaves() -> None:
    provider = Provider(answered("연락처 공유"))

    with pytest.raises(PrivacyViolationError):
        titler(provider).titles([TitleRequest("연락처 010-1234-5678 로 공유 예정")])

    assert provider.bodies == []


def test_the_log_of_a_call_is_counts() -> None:
    provider = Provider(answered("보고서 정리", "보고서를 정리함"))

    with capture_logs() as logs:
        titler(provider).titles([TitleRequest(REPORT), TitleRequest(REPORT)])

    assert logs == [
        {
            "event": "extraction_titles_written",
            "log_level": "info",
            "calls": 1,
            "rows": 2,
            "accepted": 1,
        }
    ]


# -- the rows -----------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, Team, TeamMember, Participant, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Team(id=TEAM, name="제품팀"))
        s.add(Meeting(id=MEETING, team_id=TEAM, title="주간 회의"))
        s.add(Meeting(id="mtg_other", team_id=TEAM, title="다른 회의"))
        s.flush()
        yield s


def _item(session: Session, id_: str, description: str, **over: Any) -> ExtActionItem:
    row = ExtActionItem(
        id=id_,
        meeting_id=over.pop("meeting_id", MEETING),
        description=description,
        status="needs_confirmation",
        confidence=0.9,
        origin=over.pop("origin", "model"),
        **over,
    )
    session.add(row)
    session.flush()
    return row


def _decision(session: Session, id_: str, statement: str, **over: Any) -> ExtDecision:
    row = ExtDecision(
        id=id_,
        meeting_id=MEETING,
        statement=statement,
        confidence=0.9,
        origin=over.pop("origin", "model"),
        **over,
    )
    session.add(row)
    session.flush()
    return row


def _seed(session: Session) -> None:
    _item(session, "act_1", LOGS)
    _item(session, "act_typed", "회의실 예약", origin="user")
    _item(session, "act_edited", "고객 문의 답변 정리해서 공유")
    session.add(
        ExtEditEvent(
            meeting_id=MEETING, action_item_id="act_edited", kind="edited", fields="description"
        )
    )
    _item(session, "act_titled", REPORT).title = "보고서 정리"
    _item(session, "act_elsewhere", REPORT, meeting_id="mtg_other")
    _decision(session, "dec_1", POSTPONED + " (담당 민경, 기한 2026-10-16)")
    _decision(session, "dec_typed", "환불은 7일 안에 처리", origin="user")
    _decision(session, "dec_reworded", "A안으로 진행")
    session.add(
        ExtDecisionReview(
            decision_id="dec_reworded",
            meeting_id=MEETING,
            status="confirmed",
            statement="A안으로 진행하되 일정은 다시 본다",
            reviewed_at=datetime(2026, 10, 9, tzinfo=UTC),
        )
    )
    session.flush()


def test_only_a_sentence_the_pipeline_wrote_and_has_no_title_is_titled(session: Session) -> None:
    _seed(session)

    targets = service.title_targets(session, MEETING)

    assert [(t.kind, t.id) for t in targets] == [("item", "act_1"), ("decision", "dec_1")]
    assert targets[0].asked == LOGS
    assert targets[1].asked == POSTPONED, "a decision is shown to the model without its bracket"
    assert targets[1].text == POSTPONED + " (담당 민경, 기한 2026-10-16)"


def test_a_title_is_stored_on_the_row_that_still_says_its_sentence(session: Session) -> None:
    _seed(session)
    targets = service.title_targets(session, MEETING)
    # While the model was answering: the item's sentence was corrected.
    session.get(ExtActionItem, "act_1").description = "결제 화면 오류 로그를 모아서 공유 예정"  # type: ignore[union-attr]

    written = service.store_titles(
        session, targets, ["결제 화면 오류 로그 정리", "배포 금요일로 연기"]
    )

    assert written == 1
    assert session.get(ExtActionItem, "act_1").title is None  # type: ignore[union-attr]
    assert session.get(ExtDecision, "dec_1").title == "배포 금요일로 연기"  # type: ignore[union-attr]


def test_a_row_deleted_meanwhile_and_a_row_with_no_title_are_passed_over(session: Session) -> None:
    _seed(session)
    targets = service.title_targets(session, MEETING)
    session.delete(session.get(ExtDecision, "dec_1"))
    session.flush()

    assert service.store_titles(session, targets, [None, "배포 금요일로 연기"]) == 0
    assert session.get(ExtActionItem, "act_1").title is None  # type: ignore[union-attr]


def test_another_sentence_takes_the_title_away_and_the_same_one_keeps_it(session: Session) -> None:
    item = _item(session, "act_1", LOGS)
    decision = _decision(session, "dec_1", POSTPONED)
    item.title, decision.title = "결제 화면 오류 로그 정리", "배포 금요일로 연기"
    session.flush()

    item.description, decision.statement = LOGS, POSTPONED
    assert (item.title, decision.title) == ("결제 화면 오류 로그 정리", "배포 금요일로 연기")

    item.description = "결제 화면 오류 로그를 모아서 공유 예정"
    decision.statement = "배포는 다음 달로 미루기로 함"
    assert (item.title, decision.title) == (None, None)


def test_the_title_is_kept_across_a_commit_when_the_sentence_is_written_again(
    session: Session,
) -> None:
    """After a commit the row's attributes are expired; the comparison has to
    read the stored sentence, not find nothing and drop the title."""
    item = _item(session, "act_1", LOGS)
    item.title = "결제 화면 오류 로그 정리"
    session.commit()

    item.description = LOGS

    assert item.title == "결제 화면 오류 로그 정리"


def test_a_persons_edit_of_the_description_takes_the_title_away(session: Session) -> None:
    item = _item(session, "act_1", LOGS)
    item.title = "결제 화면 오류 로그 정리"
    session.flush()

    service.update_action_item(
        session, item, ActionItemUpdate(description="결제 화면 오류 로그는 QA가 정리")
    )

    assert item.title is None
    assert service.title_targets(session, MEETING) == [], "and no model titles their sentence"


def test_the_card_and_the_row_are_given_the_title(session: Session) -> None:
    _seed(session)
    service.store_titles(
        session,
        service.title_targets(session, MEETING),
        ["결제 화면 오류 로그 정리", "배포 금요일로 연기"],
    )

    items = {i.id: i.title for i in service.list_action_items(session, meeting_id=MEETING)}
    rows = {d.id: d.title for d in service.review_for_meeting(session, MEETING).decisions}

    assert items["act_1"] == "결제 화면 오류 로그 정리"
    assert items["act_titled"] == "보고서 정리"
    assert items["act_typed"] is None
    assert rows["dec_1"] == "배포 금요일로 연기"
    assert rows["dec_typed"] is None


def test_a_reworded_decision_is_shown_without_the_models_title(session: Session) -> None:
    decision = _decision(session, "dec_1", POSTPONED)
    decision.title = "배포 금요일로 연기"
    session.flush()

    service.review_decision(
        session, decision, DecisionReviewUpdate(statement="배포는 다음 달 첫 주로 미룬다")
    )

    row = service.review_for_meeting(session, MEETING).decisions[0]
    assert row.statement == "배포는 다음 달 첫 주로 미룬다"
    assert row.title is None
    assert decision.title == "배포 금요일로 연기", "kept for the day the rewording is taken back"


# -- the task -----------------------------------------------------------------


class StubTitler:
    def __init__(self, *titles: str | None, fail: Exception | None = None) -> None:
        self.given = titles
        self.fail = fail
        self.asked: list[TitleRequest] = []
        self.roster: tuple[str, ...] = ()

    def use_roster(self, names: Any) -> None:
        self.roster = tuple(names)

    def titles(self, requests: list[TitleRequest]) -> list[Title]:
        self.asked = list(requests)
        if self.fail is not None:
            raise self.fail
        return [Title(t, "" if t else "refused") for t in self.given]


@pytest.fixture
def run(session: Session, monkeypatch: pytest.MonkeyPatch) -> Any:
    @contextmanager
    def same_session() -> Iterator[Session]:
        yield session

    monkeypatch.setattr(tasks, "session_scope", same_session)

    def _run(stub: StubTitler | None) -> tuple[int, list[dict[str, Any]]]:
        monkeypatch.setattr(tasks, "get_titler", lambda: stub)
        with capture_logs() as logs:
            written = tasks.title_meeting(MEETING)
        return written, logs

    return _run


def test_the_task_titles_the_meetings_rows(session: Session, run: Any) -> None:
    _seed(session)
    session.add(User(id="user_1", email="a@example.com", display_name="민경"))
    session.add(TeamMember(team_id=TEAM, user_id="user_1", role="member"))
    session.flush()
    stub = StubTitler("결제 화면 오류 로그 정리", None)

    written, logs = run(stub)

    assert written == 1
    assert stub.asked == [TitleRequest(LOGS, "item"), TitleRequest(POSTPONED, "decision")]
    assert "민경" in stub.roster, "the team's names are handed over to be replaced"
    assert session.get(ExtActionItem, "act_1").title == "결제 화면 오류 로그 정리"  # type: ignore[union-attr]
    assert session.get(ExtDecision, "dec_1").title is None  # type: ignore[union-attr]
    assert logs[-1] == {
        "event": "extraction_titles_stored",
        "log_level": "info",
        "meeting_id": MEETING,
        "rows": 2,
        "written": 1,
    }


def test_the_task_asks_nothing_with_no_titler_or_no_rows(session: Session, run: Any) -> None:
    assert run(None) == (0, [])

    stub = StubTitler()
    assert run(stub) == (0, [])
    assert stub.asked == []


@pytest.mark.parametrize(
    ("error", "event"),
    [
        (PrivacyViolationError("blocked"), "extraction_titles_blocked_by_privacy_guard"),
        (RuntimeError("결제 화면"), "extraction_titles_failed"),
    ],
)
def test_a_call_that_fails_leaves_the_rows_without_a_title_and_says_so_by_id(
    session: Session, run: Any, error: Exception, event: str
) -> None:
    _seed(session)

    written, logs = run(StubTitler(fail=error))

    assert written == 0
    assert session.get(ExtActionItem, "act_1").title is None  # type: ignore[union-attr]
    assert [entry["event"] for entry in logs] == [event]
    assert logs[0]["meeting_id"] == MEETING
    assert "결제" not in str(logs), "the error's class, never its text"


def test_a_titler_switched_on_without_a_key_is_said_and_asks_nothing(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unconfigured() -> None:
        raise ValueError("needs a key")

    monkeypatch.setattr(tasks, "get_titler", unconfigured)

    with capture_logs() as logs:
        assert tasks.title_meeting(MEETING) == 0

    assert [entry["event"] for entry in logs] == ["extraction_titles_not_configured"]


# -- the setting --------------------------------------------------------------


def test_titles_are_off_until_switched_on() -> None:
    assert ExtractionSettings(_env_file=None).title_impl == "none"  # type: ignore[call-arg]


def test_switching_titles_on_needs_the_acknowledgement_every_cloud_step_needs() -> None:
    with pytest.raises(ValueError, match="AUTUNE_EXTRACTION_TITLE_IMPL=llm"):
        ExtractionSettings(_env_file=None, title_impl="llm")  # type: ignore[call-arg]

    on = ExtractionSettings(_env_file=None, title_impl="llm", llm_acknowledged_392=True)  # type: ignore[call-arg]
    assert on.title_impl == "llm"
