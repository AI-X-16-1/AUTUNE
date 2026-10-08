"""A long turn is read piece by piece: each promise and each decision in it is a
row of its own (the user, 2026-10-06).

A turn longer than one request goes to the ``llm`` classifier in pieces
(``pipeline.llm``). Read back as one utterance it gave one item whose
description was the whole turn -- 3,286 characters in the live run, too long
for the resolver to summarise -- and nothing for whatever its one kind was
not. Everything here goes through the real task; the classifier is a fake that
cuts a turn at `` // `` the way the real one cuts a long one.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import autune_extraction.models  # noqa: F401  (ext_ tables)
from autune_contracts import ExtractionResult
from autune_contracts.enums import UtteranceKind
from autune_contracts.transcript import (
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptReady,
    TranscriptSource,
)
from autune_contracts.transcript import Utterance as SpokenLine
from autune_core import Base, Meeting, Participant, TeamMember, User, Utterance
from autune_extraction import service, tasks
from autune_extraction.decisions import (
    ClassifiedUtterance,
    decision_id,
    group_decisions,
    identified,
    in_pieces,
)
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionRelated,
    ExtDecisionSource,
)
from autune_extraction.pipeline import FakeClassifier, FakeNli, Prediction
from autune_extraction.pipeline.base import Resolution
from autune_extraction.pipeline.llm import _prediction, strongest

K = UtteranceKind
MEETING = "mtg_1"
CUT = " // "

DECIDED = "그럼 이번 분기는 A안으로 가기로 했습니다"
SURVEY = "설문은 제가 금요일까지 다시 쓰겠습니다"
QUOTE = "견적은 제가 수요일까지 받겠습니다"
CHAT = "오늘 날씨가 참 좋네요"
LATER = "출시는 다음 달로 미루기로 했습니다"

LONG = CUT.join([DECIDED, SURVEY, CHAT, QUOTE])


class Cut(FakeClassifier):
    """The fake, asking about a turn that holds `` // `` piece by piece."""

    def classify(self, texts: list[str]) -> list[Prediction]:
        out: list[Prediction] = []
        for text in texts:
            parts = text.split(CUT)
            kinds = [p.kind for p in super().classify(parts)]
            out.append(
                _prediction(strongest(kinds), tuple(zip(parts, kinds, strict=True)))
                if len(parts) > 1
                else _prediction(kinds[0])
            )
        return out


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (Meeting, User, TeamMember, Participant, Utterance)}
    tables = [
        t for name, t in Base.metadata.tables.items() if name in shared or name.startswith("ext_")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        s.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        s.add(Participant(id="par_1", meeting_id=MEETING, speaker_label="A", consented=True))
        s.commit()
        yield s


@pytest.fixture
def wired(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "get_classifier", Cut)
    monkeypatch.setattr(tasks, "get_nli", FakeNli)
    monkeypatch.setattr(tasks, "publish", lambda event, payload: [])
    return session


def meeting(session: Session, *lines: str) -> dict:
    """The lines stored as module A stores them, and the event that carries them."""
    spoken = []
    for number, text in enumerate(lines, start=1):
        session.add(
            Utterance(
                id=f"utt_{number}",
                meeting_id=MEETING,
                participant_id="par_1",
                speaker_label="SPEAKER_00",
                start_sec=number * 10.0,
                end_sec=number * 10.0 + 5.0,
                text=text,
                confidence=0.9,
            )
        )
        spoken.append(
            SpokenLine(
                id=f"utt_{number}",
                speaker="Speaker 1",
                start=number * 10.0,
                end=number * 10.0 + 5.0,
                text=text,
                confidence=0.9,
            )
        )
    session.commit()
    return TranscriptReady(
        meeting_id=MEETING,
        utterances=spoken,
        metadata=TranscriptMetadata(
            duration=60.0,
            source=next(iter(TranscriptSource)),
            privacy=PrivacyFlags(original_audio_deleted=True, pii_masked=True),
        ),
    ).model_dump(mode="json")


def sources_of(session: Session, decision_id: str) -> list[str]:
    return list(
        session.scalars(
            select(ExtDecisionSource.utterance_id)
            .where(ExtDecisionSource.decision_id == decision_id)
            .order_by(ExtDecisionSource.position)
        )
    )


# --- the sequence read in pieces -----------------------------------------------------


def turn(uid: str, kind: K | None, text: str, **more: object) -> ClassifiedUtterance:
    return ClassifiedUtterance(id=uid, kind=kind, confidence=0.9, text=text, **more)  # type: ignore[arg-type]


def test_a_cut_turn_is_read_as_its_pieces_and_every_other_turn_as_itself() -> None:
    whole = turn("utt_1", None, CHAT)
    cut = turn(
        "utt_2",
        K.COMMITMENT,
        LONG,
        speaker="Speaker 1",
        pieces=((DECIDED, K.DECISION), (SURVEY, K.COMMITMENT), (CHAT, None)),
    )

    read = in_pieces([whole, cut])

    assert read[0] is whole
    assert [(u.id, u.kind, u.text) for u in read[1:]] == [
        ("utt_2#1", K.DECISION, DECIDED),
        ("utt_2#2", K.COMMITMENT, SURVEY),
        ("utt_2#3", None, CHAT),
    ]
    # What a row cites is the utterance, whichever entry it was built from.
    assert {u.source_id for u in read} == {"utt_1", "utt_2"}
    assert all(u.speaker == "Speaker 1" and u.pieces == () for u in read[1:])


def test_a_turn_whose_kind_was_changed_after_classification_stays_whole() -> None:
    """Step 4 promotes an ambiguous agreement to a commitment by reading the
    whole turn; no piece is that commitment, so none can stand for it."""
    promoted = turn(
        "utt_1",
        K.COMMITMENT,
        "한번 볼게요 // 네네",
        pieces=(("한번 볼게요", K.AMBIGUOUS), ("네네", None)),
        nli_verified=True,
    )

    assert in_pieces([promoted]) == [promoted]


def test_two_decisions_of_one_turn_have_two_ids_and_a_rerun_gives_the_same_two() -> None:
    cut = turn(
        "utt_1",
        K.DECISION,
        "",
        pieces=(
            (DECIDED, K.DECISION),
            (CHAT, None),
            (CHAT, None),
            (CHAT, None),
            (LATER, K.DECISION),
        ),
    )

    def ids() -> list[str]:
        read = in_pieces([cut])
        return [id_ for id_, _group in identified(MEETING, group_decisions(read), read)]

    assert len(set(ids())) == 2
    assert ids() == ids()
    # The first is the id the turn's decision had when the turn was read whole,
    # so a person's review of it is still its review.
    assert ids()[0] == decision_id(MEETING, ["utt_1"])


# --- through the task ---------------------------------------------------------------


def test_each_promise_in_a_long_turn_is_an_item_of_its_own_citing_the_turn(
    wired: Session,
) -> None:
    tasks.on_transcript_ready(meeting(wired, CHAT, LONG))

    items = wired.query(ExtActionItem).order_by(ExtActionItem.due_text).all()
    assert [(i.description, i.due_text) for i in items] == [
        ("설문은 금요일까지 다시 쓸 예정", "금요일"),
        ("견적은 수요일까지 받을 예정", "수요일"),
    ]
    assert [[s.utterance_id for s in i.sources] for i in items] == [["utt_2"], ["utt_2"]]
    # Not the turn: neither description holds what the other piece said.
    assert all(len(i.description) < 30 for i in items)


def test_a_decision_in_the_same_turn_is_a_decision_of_its_own(wired: Session) -> None:
    """Before, the turn was a commitment and nothing else: its decision was lost."""
    tasks.on_transcript_ready(meeting(wired, CHAT, LONG))

    (decision,) = wired.query(ExtDecision).all()
    assert decision.statement == "이번 분기는 A안으로 가기로 함"
    assert decision.original_statement == DECIDED
    assert sources_of(wired, decision.id) == ["utt_2"]


def test_the_turn_itself_keeps_one_stored_kind_and_the_result_is_one_the_contract_takes(
    wired: Session,
) -> None:
    tasks.on_transcript_ready(meeting(wired, CHAT, LONG))

    assert {r.utterance_id: r.kind for r in wired.query(ExtClassification)} == {
        "utt_2": "commitment"
    }
    result = service.result_for_meeting(wired, MEETING)
    ExtractionResult.model_validate(result.model_dump(mode="json"))
    assert len(result.action_items) == 2 and len(result.decisions) == 1
    # No piece id is stored or published anywhere.
    assert "#" not in result.model_dump_json()


def test_two_decisions_settled_far_apart_in_one_turn_are_two_rows_and_a_rerun_keeps_them(
    wired: Session,
) -> None:
    event = meeting(wired, CUT.join([DECIDED, CHAT, CHAT, CHAT, LATER]))

    tasks.on_transcript_ready(event)
    first = {d.id: d.statement for d in wired.query(ExtDecision)}
    tasks.on_transcript_ready(event)

    again = {d.id: d.statement for d in wired.query(ExtDecision)}
    assert again == first and len(first) == 2
    assert sorted(first.values()) == [
        "이번 분기는 A안으로 가기로 함",
        "출시는 다음 달로 미루기로 함",
    ]
    assert [sources_of(wired, id_) for id_ in first] == [["utt_1"], ["utt_1"]]


def test_a_speaker_who_said_the_turn_was_no_promise_gets_no_item_from_any_piece(
    wired: Session,
) -> None:
    event = meeting(wired, CHAT, LONG)
    wired.add(
        ExtConfirmation(
            utterance_id="utt_2",
            meeting_id=MEETING,
            reason="ambiguous",
            resolved_kind="concern",
            sent_at=datetime.now(tz=UTC),
            responded_at=datetime.now(tz=UTC),
        )
    )
    wired.commit()

    tasks.on_transcript_ready(event)

    assert wired.query(ExtActionItem).count() == 0
    assert wired.query(ExtDecision).count() == 1


def test_a_turn_that_was_not_cut_is_one_item_as_before(wired: Session) -> None:
    tasks.on_transcript_ready(meeting(wired, DECIDED, SURVEY))

    (item,) = wired.query(ExtActionItem).all()
    assert item.description == "설문은 금요일까지 다시 쓸 예정"
    assert [s.utterance_id for s in item.sources] == ["utt_2"]
    (decision,) = wired.query(ExtDecision).all()
    assert sources_of(wired, decision.id) == ["utt_1"]


# --- what a summary cites ------------------------------------------------------------


def test_the_lines_a_piece_summary_used_are_stored_as_the_utterances_they_are_in(
    session: Session,
) -> None:
    """A resolver that cites is handed pieces as lines; what it cites is kept
    as the turn, once, and never as the turn the item itself is from."""
    event = meeting(session, CHAT, LONG, "네 좋습니다")
    spoken = TranscriptReady.model_validate(event).utterances
    read = in_pieces(
        [
            turn("utt_1", None, CHAT),
            turn(
                "utt_2",
                K.COMMITMENT,
                LONG,
                pieces=((DECIDED, K.DECISION), (SURVEY, K.COMMITMENT), (CHAT, None)),
            ),
            turn("utt_3", None, "네 좋습니다"),
        ]
    )

    (item,) = service.build_action_items(
        session,
        meeting_id=MEETING,
        utterances=spoken,
        classified=read,
        resolved={"utt_2#2": "설문 문항을 금요일까지 다시 쓰기"},
        related={"utt_2#2": ["utt_2#1", "utt_3", "utt_1", "utt_3", "utt_unknown"]},
    )

    assert item.description == "설문 문항을 금요일까지 다시 쓰기" and item.description_resolved
    assert [r.utterance_id for r in item.related] == ["utt_3", "utt_1"]

    ((made, _group),) = identified(MEETING, group_decisions(read), read)
    assert made == decision_id(MEETING, ["utt_2"])
    service.build_decisions(
        session,
        meeting_id=MEETING,
        utterances=read,
        summaries={made: Resolution("A안으로 가기", used=("utt_2#2", "utt_3", "utt_3"))},
    )
    session.flush()

    assert sources_of(session, made) == ["utt_2"]
    assert list(
        session.scalars(
            select(ExtDecisionRelated.utterance_id).where(ExtDecisionRelated.decision_id == made)
        )
    ) == ["utt_3"]


# --- which part of the turn a row was made from --------------------------------------


def test_a_row_made_from_a_piece_quotes_that_piece_and_keeps_only_where_it_is(
    wired: Session,
) -> None:
    """The reader is shown the sentence, not the minute it was said in (the
    user, 2026-10-08) -- cut from the stored turn, of which no word is copied."""
    tasks.on_transcript_ready(meeting(wired, CHAT, LONG))

    items = wired.query(ExtActionItem).order_by(ExtActionItem.due_text).all()
    quoted = [service.source_utterances(wired, item.id) for item in items]
    assert [[(s.id, s.excerpt) for s in sources] for sources in quoted] == [
        [("utt_2", SURVEY)],
        [("utt_2", QUOTE)],
    ]
    # The whole turn is still beside it for a reader who asks.
    assert all(s.text == LONG for sources in quoted for s in sources)
    assert [
        LONG[row.excerpt_start : row.excerpt_end]
        for row in wired.query(ExtActionItemSource).order_by(ExtActionItemSource.excerpt_start)
    ] == [SURVEY, QUOTE]

    (decision,) = wired.query(ExtDecision).all()
    detail = service.read_decision_detail(wired, decision)
    assert [(s.id, s.excerpt, s.text) for s in detail.sources] == [("utt_2", DECIDED, LONG)]
    # And the 요약 tab has the same sentence beneath the decision's line.
    assert [d.summary for d in service.meeting_summary(wired, MEETING).decisions] == [DECIDED]


def test_a_turn_that_was_not_cut_has_no_part_and_is_quoted_whole(wired: Session) -> None:
    tasks.on_transcript_ready(meeting(wired, DECIDED, SURVEY))

    (item,) = wired.query(ExtActionItem).all()
    assert [(s.text, s.excerpt) for s in service.source_utterances(wired, item.id)] == [
        (SURVEY, None)
    ]
    (decision,) = wired.query(ExtDecision).all()
    assert [s.excerpt for s in service.read_decision_detail(wired, decision).sources] == [None]
    assert wired.query(ExtActionItemSource).one().excerpt_start is None
    assert wired.query(ExtDecisionSource).one().excerpt_start is None


def test_a_rerun_gives_a_decision_from_before_the_offsets_its_part(wired: Session) -> None:
    """A decision keeps its id and its source rows across runs, so the part is
    written for every decision of a run and not only for a new one."""
    event = meeting(wired, CHAT, LONG)
    tasks.on_transcript_ready(event)
    wired.execute(update(ExtDecisionSource).values(excerpt_start=None, excerpt_end=None))
    wired.commit()
    (before,) = wired.query(ExtDecision.id).all()

    tasks.on_transcript_ready(event)

    (decision,) = wired.query(ExtDecision).all()
    assert (decision.id,) == tuple(before)
    wired.expire_all()
    assert [s.excerpt for s in service.read_decision_detail(wired, decision).sources] == [DECIDED]


def test_a_rerun_that_finds_no_part_drops_the_one_recorded_before(wired: Session) -> None:
    """Offsets a run did not write are not that run's: left on the row they
    would cut the decision's quotation at a place nothing chose."""
    event = meeting(wired, DECIDED, SURVEY)
    tasks.on_transcript_ready(event)
    wired.execute(update(ExtDecisionSource).values(excerpt_start=0, excerpt_end=3))
    wired.commit()

    tasks.on_transcript_ready(event)

    wired.expire_all()
    (row,) = wired.query(ExtDecisionSource).all()
    assert (row.excerpt_start, row.excerpt_end) == (None, None)
    (decision,) = wired.query(ExtDecision).all()
    assert [(s.text, s.excerpt) for s in service.read_decision_detail(wired, decision).sources] == [
        (DECIDED, None)
    ]


def test_a_card_shows_the_part_beneath_a_summary_and_nothing_beneath_the_line_itself(
    session: Session,
) -> None:
    """``summary`` is the words a model's sentence stands for. An item whose
    description is the line itself has nothing to add beneath it."""
    spoken = TranscriptReady.model_validate(meeting(session, CHAT, LONG)).utterances
    read = in_pieces(
        [
            turn("utt_1", None, CHAT),
            turn(
                "utt_2",
                K.COMMITMENT,
                LONG,
                pieces=((DECIDED, K.DECISION), (SURVEY, K.COMMITMENT), (QUOTE, K.COMMITMENT)),
            ),
        ]
    )

    summarised, as_said = service.build_action_items(
        session,
        meeting_id=MEETING,
        utterances=spoken,
        classified=read,
        resolved={"utt_2#2": "설문 문항을 금요일까지 다시 쓰기"},
    )

    assert summarised.description_resolved and not as_said.description_resolved
    assert service.action_item_summaries(session, [summarised, as_said]) == {summarised.id: SURVEY}


def test_a_summary_from_before_the_offsets_shows_the_start_of_the_whole_turn(
    session: Session,
) -> None:
    """No backfill (the user, 2026-10-08): an older row is quoted as it always
    was, and the card cuts it short."""
    long_turn = CUT.join([DECIDED, SURVEY, CHAT, QUOTE, LATER])
    spoken = TranscriptReady.model_validate(meeting(session, long_turn)).utterances
    read = in_pieces(
        [turn("utt_1", K.COMMITMENT, long_turn, pieces=((DECIDED, None), (SURVEY, K.COMMITMENT)))]
    )
    (item,) = service.build_action_items(
        session,
        meeting_id=MEETING,
        utterances=spoken,
        classified=read,
        resolved={"utt_1#2": "설문 문항을 금요일까지 다시 쓰기"},
    )
    item.sources[0].excerpt_start = item.sources[0].excerpt_end = None
    session.flush()

    preview = service.action_item_summaries(session, [item])[item.id]

    assert len(long_turn) > service.SUMMARY_MAX_CHARS
    assert preview == long_turn[: service.SUMMARY_MAX_CHARS - 1].rstrip() + "…"
