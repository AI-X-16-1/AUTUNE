"""The summary of an ambiguous agreement, written once its speaker confirms it.

Decided with the user (2026-10-01): the DM shows only the speaker's line; an
answer of "약속입니다" makes the draft at once, and a job then writes the summary
onto it -- shown on the board, the line beneath it. Under test: the job is sent
for that answer only; it writes the summary and keeps only consenting cited
lines; it leaves a touched draft or a changed answer alone; a rerun keeps the
summary; and nothing is summarised for an agreement nobody confirmed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_contracts.enums import UtteranceKind
from autune_core import Base, Meeting, Participant, TeamMember, User, Utterance
from autune_extraction import service, tasks
from autune_extraction.confirmations import ConfirmationResponse, build_confirmation_dm
from autune_extraction.decisions import ClassifiedUtterance
from autune_extraction.models import ExtActionItem, ExtActionItemRelated, ExtConfirmation
from autune_extraction.noun_form import tidy
from autune_extraction.pipeline.base import Resolution, ResolutionRequest

MEETING = "mtg_1"
SUMMARY = "결제 로그 필드 추가 제가 이번 주 안에 볼게요"
LINES = [
    ("utt_1", "par_kim", "결제 로그 필드 얘기 제가 꺼냈었죠"),
    ("utt_2", "par_lee", "그 필드는 정산팀도 쓴다고 했어요"),
    ("utt_3", "par_kim", "그럼 제가 한번 볼게요"),
    ("utt_4", "par_no", "제 번호로 연락 주세요"),
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    shared = {m.__tablename__ for m in (Meeting, User, Participant, Utterance, TeamMember)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        s.add(User(id="user_kim", email="kim@example.com", display_name="김민경"))
        s.add_all(
            [
                Participant(
                    id="par_kim",
                    meeting_id=MEETING,
                    speaker_label="김",
                    consented=True,
                    user_id="user_kim",
                ),
                Participant(id="par_lee", meeting_id=MEETING, speaker_label="이", consented=True),
                Participant(id="par_no", meeting_id=MEETING, speaker_label="박", consented=False),
            ]
        )
        for n, (uid, who, text) in enumerate(LINES):
            s.add(
                Utterance(
                    id=uid,
                    meeting_id=MEETING,
                    participant_id=who,
                    speaker_label="화자",
                    start_sec=float(n),
                    end_sec=float(n) + 0.5,
                    text=text,
                )
            )
        s.add(
            ExtConfirmation(
                utterance_id="utt_3",
                meeting_id=MEETING,
                reason="weak_assent",
                sent_at=datetime.now(UTC),
            )
        )
        s.commit()
        yield s


class _Resolver:
    """Writes ``SUMMARY``, citing a consenting line, a non-consenting one and an
    id from nowhere; counts its calls."""

    model_version = "test"

    def __init__(self) -> None:
        self.calls: list[list[ResolutionRequest]] = []

    def resolve(self, requests: list[ResolutionRequest]) -> list[str]:
        return [r.target for r in requests]

    def resolve_with_evidence(self, requests: list[ResolutionRequest]) -> list[Resolution]:
        self.calls.append(requests)
        return [Resolution(SUMMARY, used=("utt_1", "utt_4", "utt_elsewhere")) for _ in requests]


@pytest.fixture
def wired(session: Session, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Both modules on this session; the job recorded, not sent; a fake model."""

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    queued: list[str] = []
    resolver = _Resolver()
    monkeypatch.setattr(service, "session_scope", scope)
    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks.summarise_confirmed_draft, "delay", queued.append)
    monkeypatch.setattr(tasks, "get_resolver", lambda: resolver)
    return {"queued": queued, "resolver": resolver}


def answer(kind: UtteranceKind) -> None:
    service.apply_confirmation_response(
        ConfirmationResponse(utterance_id="utt_3", resolved_kind=kind, responder_id="user_kim")
    )


def draft(session: Session) -> ExtActionItem:
    (row,) = session.scalars(select(ExtActionItem)).all()
    return row


def test_the_dm_still_quotes_only_the_line() -> None:
    _, blocks = build_confirmation_dm(
        utterance_id="utt_3", quoted_text="그럼 제가 한번 볼게요", answer_url="https://a/x"
    )
    assert "> 그럼 제가 한번 볼게요" in str(blocks)


def test_yes_drafts_the_line_at_once_and_sends_the_summary_job(
    session: Session, wired: dict
) -> None:
    answer(UtteranceKind.COMMITMENT)

    assert draft(session).description == tidy("그럼 제가 한번 볼게요")
    assert wired["queued"] == ["utt_3"]
    assert wired["resolver"].calls == [], "no model call in the click"


def test_any_other_answer_sends_no_job(session: Session, wired: dict) -> None:
    answer(UtteranceKind.DECISION)

    assert wired["queued"] == []


def test_the_job_puts_the_summary_on_the_draft(session: Session, wired: dict) -> None:
    answer(UtteranceKind.COMMITMENT)

    tasks.summarise_confirmed_draft("utt_3")

    row = draft(session)
    assert row.description == tidy(SUMMARY)
    assert row.description_resolved is True
    assert [s.utterance_id for s in row.sources] == ["utt_3"], "the line stays its source"
    assert list(session.scalars(select(ExtActionItemRelated.utterance_id))) == ["utt_1"]
    (request,) = wired["resolver"].calls[0]
    sent = [request.target, *request.context, *request.context_after]
    assert "제 번호로 연락 주세요" not in sent, "a non-consenting line never reaches the model"


def test_the_job_keeps_a_line_the_draft_already_cites(session: Session, wired: dict) -> None:
    """The pipeline's own draft may already cite the line the summary cites. A new
    row for it was inserted before the old one was deleted, and broke the unique
    key (real-service check, 2026-10-01)."""
    answer(UtteranceKind.COMMITMENT)
    draft(session).related = [ExtActionItemRelated(utterance_id="utt_1")]
    session.commit()

    tasks.summarise_confirmed_draft("utt_3")

    assert draft(session).description == tidy(SUMMARY)
    assert list(session.scalars(select(ExtActionItemRelated.utterance_id))) == ["utt_1"]


def test_a_draft_a_person_touched_is_left_alone(session: Session, wired: dict) -> None:
    answer(UtteranceKind.COMMITMENT)
    row = draft(session)
    service.update_action_item(
        session, row, service.ActionItemUpdate(description="사람이 고친 문장")
    )
    session.commit()

    tasks.summarise_confirmed_draft("utt_3")

    assert draft(session).description == "사람이 고친 문장"
    assert wired["resolver"].calls == [], "nothing to summarise, so no model call"


def test_a_changed_answer_takes_the_draft_and_the_job_does_nothing(
    session: Session, wired: dict
) -> None:
    answer(UtteranceKind.COMMITMENT)
    answer(UtteranceKind.CONCERN)

    tasks.summarise_confirmed_draft("utt_3")

    assert session.scalars(select(ExtActionItem)).all() == []


def test_a_rerun_rebuilds_the_draft_with_its_summary(session: Session, wired: dict) -> None:
    answer(UtteranceKind.COMMITMENT)
    classified = [
        ClassifiedUtterance(id=uid, kind=None, confidence=0.5, text="" if who == "par_no" else text)
        for uid, who, text in LINES
    ]
    confirmed = service.confirmed_commitment_ids(session, MEETING)
    summaries = service.confirmed_summaries(wired["resolver"], classified, confirmed)

    service.build_action_items(
        session,
        meeting_id=MEETING,
        utterances=service.stored_transcript(session, MEETING),
        classified=classified,
        confirmed=summaries,
    )

    assert confirmed == {"utt_3"}
    assert draft(session).description == tidy(SUMMARY)


def test_no_confirmed_agreement_costs_no_model_call(session: Session) -> None:
    resolver = _Resolver()

    assert service.confirmed_summaries(resolver, [], set()) == {}
    assert resolver.calls == []
