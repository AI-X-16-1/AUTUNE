"""Pipeline steps 1 and 5 against a real session: classify, store, group decisions.

SQLite in memory, the fake classifier, and the task called directly. What is
under test is what a second run leaves behind, what an utterance the model calls
none turns into, and which order the rows come back in -- none of it visible
without a store.

This is not the integration suite: no Postgres, no migrations, no broker.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from autune_contracts import (
    EXTRACTION_COMPLETED,
    ExtractionResult,
    TranscriptReady,
    validate_major_version,
)
from autune_contracts.enums import UtteranceKind
from autune_contracts.transcript import (
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptSource,
    Utterance,
)
from autune_core import Base, Meeting, Participant
from autune_core import Utterance as StoredUtterance
from autune_extraction import service, tasks
from autune_extraction.models import (
    ExtActionItem,
    ExtActionItemSource,
    ExtClassification,
    ExtConfirmation,
    ExtDecision,
    ExtDecisionRef,
    ExtDecisionReview,
    ExtDecisionSource,
    ExtEditEvent,
)
from autune_extraction.pipeline import FakeClassifier, FakeNli, Prediction

K = UtteranceKind
MEETING = "mtg_1"

TABLES = [
    Meeting.__table__,
    Participant.__table__,
    StoredUtterance.__table__,
    ExtClassification.__table__,
    ExtDecision.__table__,
    ExtDecisionRef.__table__,
    ExtDecisionSource.__table__,
    ExtDecisionReview.__table__,
    # The task drafts action items too (step 3).
    ExtActionItem.__table__,
    ExtActionItemSource.__table__,
    ExtEditEvent.__table__,
    ExtConfirmation.__table__,
]

# Endings the fake reads: 겠습니다 commitment, 기로 했 decision, 나요 question,
# 볼게요 ambiguous; anything else none.
LINES = [
    ("utt_1", 0.0, "그럼 이번 분기는 A안으로 가기로 했습니다"),
    ("utt_2", 4.0, "네 좋아요"),
    ("utt_3", 8.0, "제가 금요일까지 정리하겠습니다"),
    ("utt_4", 12.0, "예산은 언제 나오나요"),
    ("utt_5", 16.0, "한번 볼게요"),
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        session.add(Meeting(id=MEETING, team_id="team_1", title="주간 회의"))
        yield session


def spoken(lines: list[tuple[str, float, str]] = LINES) -> list[Utterance]:
    return [
        Utterance(
            id=uid, speaker="Speaker 1", start=start, end=start + 3.0, text=text, confidence=0.9
        )
        for uid, start, text in lines
    ]


def stored(
    session: Session,
    lines: list[tuple[str, float, str]] = LINES,
    *,
    speaker_of: dict[str, str | None] | None = None,
) -> None:
    """The rows module A wrote: the spoken-order join reads them, and so does the
    consent filter.

    Every line is spoken by the consenting ``par_yes`` unless ``speaker_of`` says
    otherwise -- ``par_no`` did not consent, and ``None`` is speech with no
    participant behind it.
    """
    if session.get(Participant, "par_yes") is None:
        session.add_all(
            [
                Participant(id="par_yes", meeting_id=MEETING, speaker_label="A", consented=True),
                Participant(id="par_no", meeting_id=MEETING, speaker_label="B", consented=False),
            ]
        )
    speaker_of = speaker_of or {}
    for uid, start, text in lines:
        session.add(
            StoredUtterance(
                id=uid,
                meeting_id=MEETING,
                participant_id=speaker_of.get(uid, "par_yes"),
                speaker_label="SPEAKER_00",
                start_sec=start,
                end_sec=start + 3.0,
                text=text,
            )
        )
    session.flush()


def run(session: Session, classifier: object = None, lines=LINES) -> int:
    classifier = classifier or FakeClassifier()
    classified = service.classify_utterances(
        classifier,  # type: ignore[arg-type]
        spoken(lines),
        consented={uid for uid, _, _ in lines},
    )
    count = service.store_classifications(
        session,
        meeting_id=MEETING,
        utterances=classified,
        model_version=classifier.model_version,  # type: ignore[attr-defined]
    )
    service.build_decisions(session, meeting_id=MEETING, utterances=classified)
    return count


def kinds(session: Session) -> dict[str, str]:
    rows = session.scalars(select(ExtClassification)).all()
    return {row.utterance_id: row.kind for row in rows}


# --- step 1: what is stored ---------------------------------------------------


def test_only_the_five_kinds_are_stored_and_none_is_absent(session: Session) -> None:
    """``네 좋아요`` is none of the kinds, so it has no row -- the same way the
    contract says it, by absence (#149)."""
    count = run(session)

    assert count == 4
    assert kinds(session) == {
        "utt_1": "decision",
        "utt_3": "commitment",
        "utt_4": "open_question",
        "utt_5": "ambiguous",
    }


def test_every_row_names_the_model_that_wrote_it(session: Session) -> None:
    run(session)

    versions = set(session.scalars(select(ExtClassification.model_version)).all())

    assert versions == {"fake"}


def test_running_twice_leaves_one_set_of_rows(session: Session) -> None:
    """A redelivered task, or a meeting reprocessed after a correction."""
    run(session)
    run(session)

    assert session.query(ExtClassification).count() == 4
    assert session.query(ExtDecision).count() == 1


def test_a_rerun_keeps_each_decisions_id_and_one_set_of_sources(session: Session) -> None:
    """#171: D keys a lineage on the ``dec_`` id, so a rebuild over the same
    labels must hand it the same one. And with ids that repeat, the previous
    run's source rows must not attach themselves to the rebuilt decision --
    SQLite, like this suite, enforces no cascade."""
    run(session)
    first = [decision.id for decision in session.query(ExtDecision)]

    run(session)

    assert [decision.id for decision in session.query(ExtDecision)] == first
    assert session.query(ExtDecisionSource).count() == 1


class _EverythingIsNone:
    """A retrained model that no longer sees a kind anywhere in this meeting."""

    model_version = "retrained"

    def classify(self, texts: list[str]) -> list[Prediction]:
        return [
            Prediction(kind=None, confidence=0.9, scores=dict.fromkeys(K, 0.02), none_score=0.9)
            for _ in texts
        ]


def test_a_rerun_drops_a_label_the_new_model_no_longer_gives(session: Session) -> None:
    """Why this replaces rather than merges.

    An utterance that is now none has no new row, so a merge keyed on the
    utterance would have nothing to overwrite the old label with, and the
    meeting would keep a commitment the current model does not see.
    """
    run(session)
    run(session, _EverythingIsNone())

    assert kinds(session) == {}
    assert session.query(ExtDecision).count() == 0


def test_another_meetings_rows_are_left_alone(session: Session) -> None:
    session.add(
        ExtClassification(
            utterance_id="utt_other",
            meeting_id="mtg_2",
            kind="concern",
            confidence=0.7,
            model_version="fake",
            nli_verified=False,
        )
    )
    session.flush()

    run(session)

    assert kinds(session)["utt_other"] == "concern"


# --- step 1: order and shape --------------------------------------------------


def test_utterances_are_classified_in_spoken_order_whatever_the_payload_order(
    session: Session,
) -> None:
    classified = service.classify_utterances(
        FakeClassifier(), list(reversed(spoken())), consented={uid for uid, _, _ in LINES}
    )

    assert [u.id for u in classified] == ["utt_1", "utt_2", "utt_3", "utt_4", "utt_5"]
    assert classified[1].kind is None, "none stays in the sequence, as None"


class _Short:
    model_version = "short"

    def classify(self, texts: list[str]) -> list[Prediction]:
        return FakeClassifier().classify(texts[:-1])


def test_a_classifier_that_returns_too_few_answers_is_refused() -> None:
    """Zipping a short list would label the wrong utterances, silently."""
    with pytest.raises(ValueError, match="predictions"):
        service.classify_utterances(
            _Short(),  # type: ignore[arg-type]
            spoken(),
            consented={uid for uid, _, _ in LINES},
        )


def test_the_contract_view_is_in_spoken_order(session: Session) -> None:
    """The row has no position; the join to ``utterances`` supplies it."""
    stored(session)
    run(session, lines=list(reversed(LINES)))

    classifications = service.classifications_for_meeting(session, MEETING)

    assert [c.utterance_id for c in classifications] == ["utt_1", "utt_3", "utt_4", "utt_5"]
    assert classifications[0].kind is K.DECISION
    assert not any(c.nli_verified for c in classifications), "step 4 does not exist yet"


# --- step 5: decisions are counted across the none utterances -----------------


def test_none_utterances_count_in_the_decision_gap(session: Session) -> None:
    """Most of the utterances between two decisions are none of the kinds.

    Dropping them before grouping would close the gap and weld two decisions
    three turns apart into one statement.
    """
    lines = [
        ("utt_a", 0.0, "그 부분은 B안으로 가기로 했습니다"),
        ("utt_b", 2.0, "네"),
        ("utt_c", 4.0, "음"),
        ("utt_d", 6.0, "그렇죠"),
        ("utt_e", 8.0, "일정은 다음 주로 하기로 했습니다"),
    ]

    run(session, lines=lines)

    assert session.query(ExtDecision).count() == 2


# --- consent: privacy.md section 5 ----------------------------------------------


class _Recording(FakeClassifier):
    """The fake, remembering every text it was shown."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[str] = []

    def classify(self, texts: list[str]) -> list[Prediction]:
        self.seen.extend(texts)
        return super().classify(texts)


def test_the_consent_filter_reads_participants_not_the_payload(session: Session) -> None:
    """Consenting speech is in; a speaker who did not consent, and speech with no
    participant at all, are out -- unknown is not yes."""
    stored(session, speaker_of={"utt_1": "par_no", "utt_5": None})

    assert service.consented_utterance_ids(session, MEETING) == {"utt_2", "utt_3", "utt_4"}


def test_a_non_consenting_turn_still_counts_in_the_decision_gap(session: Session) -> None:
    """Their words are not read, but someone spoke there.

    Three turns by a speaker who did not consent sit between two decisions. They
    reach the grouping as turns with no kind and no text, so the gap is still
    three and the decisions stay two. Dropping the turns instead would close the
    gap and weld the decisions into one statement.
    """
    lines = [
        ("utt_a", 0.0, "그 부분은 B안으로 가기로 했습니다"),
        ("utt_b", 2.0, "저는 반대입니다"),
        ("utt_c", 4.0, "그건 좀 이상한데요"),
        ("utt_d", 6.0, "다시 생각해 보시죠"),
        ("utt_e", 8.0, "일정은 다음 주로 하기로 했습니다"),
    ]
    classifier = _Recording()

    classified = service.classify_utterances(
        classifier, spoken(lines), consented={"utt_a", "utt_e"}
    )
    service.build_decisions(session, meeting_id=MEETING, utterances=classified)

    assert classifier.seen == [
        "그 부분은 B안으로 가기로 했습니다",
        "일정은 다음 주로 하기로 했습니다",
    ]
    assert [u.text for u in classified if u.id in {"utt_b", "utt_c", "utt_d"}] == ["", "", ""]
    assert session.query(ExtDecision).count() == 2


# --- the task ------------------------------------------------------------------


def transcript(*, masked: bool = True) -> dict:
    return TranscriptReady(
        meeting_id=MEETING,
        utterances=spoken(),
        metadata=TranscriptMetadata(
            duration=20.0,
            source=next(iter(TranscriptSource)),
            privacy=PrivacyFlags(original_audio_deleted=True, pii_masked=masked),
        ),
    ).model_dump(mode="json")


@pytest.fixture
def wired(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    """The task with this session behind ``session_scope`` and the fake behind
    the registry, committing the way ``session_scope`` does."""

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "get_classifier", FakeClassifier)
    monkeypatch.setattr(tasks, "get_nli", FakeNli)
    return session


def test_the_task_classifies_and_groups_a_meeting(wired: Session) -> None:
    stored(wired)

    tasks.on_transcript_ready(transcript())

    assert len(kinds(wired)) == 4
    assert wired.query(ExtDecision).count() == 1
    assert wired.query(ExtConfirmation).count() == 1, "한번 볼게요, recorded and not asked"


def test_the_task_runs_step_4_on_commitment_and_ambiguous_rows_only(wired: Session) -> None:
    """utt_3 (commitment) and utt_5 (ambiguous) both carry a real commitment or
    weak-assent marker the fake NLI reads; utt_1 (decision) and utt_4
    (open_question) are never asked."""
    stored(wired)

    tasks.on_transcript_ready(transcript())

    verified = {
        row.utterance_id: row.nli_verified for row in wired.scalars(select(ExtClassification))
    }
    assert verified == {
        "utt_1": False,
        "utt_3": True,
        "utt_4": False,
        "utt_5": True,
    }


def test_the_task_refuses_an_unmasked_transcript_before_classifying(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant 11: nothing reads the text of a transcript A did not mask."""

    def must_not_be_called() -> None:
        raise AssertionError("the classifier was reached")

    monkeypatch.setattr(tasks, "get_classifier", must_not_be_called)

    with pytest.raises(ValueError, match="not PII-masked"):
        tasks.on_transcript_ready(transcript(masked=False))

    assert kinds(wired) == {}


def test_the_task_never_shows_a_non_consenting_speaker_to_the_classifier(
    wired: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decision (utt_1) and the commitment (utt_3) are spoken by someone who
    did not consent, and utt_5 has no participant at all.

    None of the three reaches the classifier, none gets an ``ext_classifications``
    row, and the decision they would have made is not written either -- its
    sentence would otherwise sit in ``ext_decisions.statement``.
    """
    stored(wired, speaker_of={"utt_1": "par_no", "utt_3": "par_no", "utt_5": None})
    classifier = _Recording()
    monkeypatch.setattr(tasks, "get_classifier", lambda: classifier)

    tasks.on_transcript_ready(transcript())

    assert classifier.seen == ["네 좋아요", "예산은 언제 나오나요"]
    assert kinds(wired) == {"utt_4": "open_question"}
    assert wired.query(ExtDecision).count() == 0


# --- step 8: publishing ExtractionResult (#31) -------------------------------------


@pytest.fixture
def published(wired: Session, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    """Every ``publish`` the task makes, instead of the broker."""
    sent: list[tuple[str, dict]] = []

    def record(event: str, payload: dict) -> list[str]:
        sent.append((event, payload))
        return []

    monkeypatch.setattr(tasks, "publish", record)
    return sent


def test_the_task_publishes_what_it_stored(
    wired: Session, published: list[tuple[str, dict]]
) -> None:
    """What D and E receive is the table, read the way ``GET /results`` reads it."""
    stored(wired)

    tasks.on_transcript_ready(transcript())

    ((event, payload),) = published
    assert event == EXTRACTION_COMPLETED
    result = ExtractionResult.model_validate(payload)
    validate_major_version(result)  # what D's and E's consumers do first
    assert result == service.result_for_meeting(wired, MEETING)
    assert [d.id for d in result.decisions] == [d.id for d in wired.query(ExtDecision)]
    assert len(result.classifications) == 4
    assert len(result.action_items) == 1
    assert [a.utterance_id for a in result.ambiguous_agreements] == ["utt_5"]


def test_every_run_publishes_the_ids_now_in_the_table(
    wired: Session, published: list[tuple[str, dict]]
) -> None:
    """A rerun publishes too, with the ids now in the table. Since #171 a rerun
    over the same utterances keeps each ``dec_`` id, but a decision whose
    sources changed gets a new one -- and module A mints new ``utt_`` ids
    whenever it reprocesses a recording (#194) -- so a rerun that stayed quiet
    could leave D holding ids that are gone."""
    stored(wired)

    tasks.on_transcript_ready(transcript())
    tasks.on_transcript_ready(transcript())

    assert len(published) == 2
    latest = ExtractionResult.model_validate(published[1][1])
    assert [d.id for d in latest.decisions] == [d.id for d in wired.query(ExtDecision)]


def test_the_result_goes_out_after_the_writes_commit(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two modules act on this event; a rollback after it had gone would be two
    modules analysing a result that was never stored. The scope records where it
    committed, so moving ``publish`` inside the write transaction changes the
    order this sees."""
    order: list[str] = []

    @contextmanager
    def scope() -> Iterator[Session]:
        yield session
        session.commit()
        order.append("commit")

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "get_classifier", FakeClassifier)
    monkeypatch.setattr(tasks, "get_nli", FakeNli)
    monkeypatch.setattr(tasks, "publish", lambda event, payload: order.append("publish") or [])
    stored(session)

    tasks.on_transcript_ready(transcript())

    # The consent read, then the writes, then the event.
    assert order == ["commit", "commit", "publish"]


def test_nothing_is_published_when_the_writes_fail(
    wired: Session, published: list[tuple[str, dict]], monkeypatch: pytest.MonkeyPatch
) -> None:
    stored(wired)

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the write failed")

    monkeypatch.setattr(service, "build_decisions", broken)

    with pytest.raises(RuntimeError, match="the write failed"):
        tasks.on_transcript_ready(transcript())

    assert published == []


def test_nothing_is_published_for_a_transcript_it_refuses(
    wired: Session, published: list[tuple[str, dict]]
) -> None:
    with pytest.raises(ValueError, match="not PII-masked"):
        tasks.on_transcript_ready(transcript(masked=False))

    assert published == []
