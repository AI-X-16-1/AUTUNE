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

from autune_contracts import TranscriptReady
from autune_contracts.enums import UtteranceKind
from autune_contracts.transcript import (
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptSource,
    Utterance,
)
from autune_core import Base, Meeting
from autune_core import Utterance as StoredUtterance
from autune_extraction import service, tasks
from autune_extraction.models import ExtClassification, ExtDecision, ExtDecisionSource
from autune_extraction.pipeline import FakeClassifier, Prediction

K = UtteranceKind
MEETING = "mtg_1"

TABLES = [
    Meeting.__table__,
    StoredUtterance.__table__,
    ExtClassification.__table__,
    ExtDecision.__table__,
    ExtDecisionSource.__table__,
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


def stored(session: Session, lines: list[tuple[str, float, str]] = LINES) -> None:
    """The rows module A wrote. Needed for the spoken-order join, nothing else."""
    for uid, start, text in lines:
        session.add(
            StoredUtterance(
                id=uid,
                meeting_id=MEETING,
                speaker_label="SPEAKER_00",
                start_sec=start,
                end_sec=start + 3.0,
                text=text,
            )
        )
    session.flush()


def run(session: Session, classifier: object = None, lines=LINES) -> int:
    classifier = classifier or FakeClassifier()
    classified = service.classify_utterances(classifier, spoken(lines))  # type: ignore[arg-type]
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
    classified = service.classify_utterances(FakeClassifier(), list(reversed(spoken())))

    assert [u.id for u in classified] == ["utt_1", "utt_2", "utt_3", "utt_4", "utt_5"]
    assert classified[1].kind is None, "none stays in the sequence, as None"


class _Short:
    model_version = "short"

    def classify(self, texts: list[str]) -> list[Prediction]:
        return FakeClassifier().classify(texts[:-1])


def test_a_classifier_that_returns_too_few_answers_is_refused() -> None:
    """Zipping a short list would label the wrong utterances, silently."""
    with pytest.raises(ValueError, match="predictions"):
        service.classify_utterances(_Short(), spoken())  # type: ignore[arg-type]


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
    return session


def test_the_task_classifies_and_groups_a_meeting(wired: Session) -> None:
    tasks.on_transcript_ready(transcript())

    assert len(kinds(wired)) == 4
    assert wired.query(ExtDecision).count() == 1


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
