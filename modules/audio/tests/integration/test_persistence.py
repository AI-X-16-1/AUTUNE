"""Writing the transcript down, twice.

`acks_late` means a worker that dies after writing and before acknowledging gets
the same recording again. Issue #9 asks for a task that is safe to run twice,
and "safe" has to mean the second run leaves the database as the first did —
not merely that it does not crash.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_audio.persistence import persist_transcript
from autune_audio.speakers import Utterance as SpokenUtterance
from autune_core.entities import Meeting, Participant, Utterance
from autune_core.errors import NotFoundError, PrivacyViolationError

pytestmark = pytest.mark.integration


def spoken(speaker: str, start: float, end: float, text: str) -> SpokenUtterance:
    return SpokenUtterance(
        speaker=speaker, start=start, end=end, text=text, words=(), confidence=0.9
    )


TRANSCRIPT = (
    spoken("SPEAKER_00", 0.0, 4.0, "다음 회의는 금요일 오후 3시입니다"),
    spoken("SPEAKER_01", 4.2, 7.0, "네 그때 뵙겠습니다"),
    spoken("SPEAKER_00", 7.2, 9.0, "수고하셨습니다"),
)


def rows(session: Session, meeting_id: str) -> list[Utterance]:
    return list(
        session.scalars(
            sa.select(Utterance)
            .where(Utterance.meeting_id == meeting_id)
            .order_by(Utterance.start_sec)
        )
    )


class TestRunningItTwice:
    def test_the_second_run_leaves_the_same_rows(self, db_session: Session, meeting: str) -> None:
        first = persist_transcript(
            db_session,
            meeting_id=meeting,
            utterances=TRANSCRIPT,
            duration_seconds=9.0,
            audio_deleted=True,
        )
        second = persist_transcript(
            db_session,
            meeting_id=meeting,
            utterances=TRANSCRIPT,
            duration_seconds=9.0,
            audio_deleted=True,
        )

        assert first.utterances == second.utterances == 3
        assert [u.text for u in rows(db_session, meeting)] == [u.text for u in TRANSCRIPT]

    def test_a_shorter_second_run_replaces_rather_than_leaves_orphans(
        self, db_session: Session, meeting: str
    ) -> None:
        """A different glossary or a fallback pass cuts the speech differently.

        The utterance count is not stable between runs, which is why the write
        replaces the set instead of upserting by a derived id.
        """
        persist_transcript(
            db_session,
            meeting_id=meeting,
            utterances=TRANSCRIPT,
            duration_seconds=9.0,
            audio_deleted=True,
        )
        persist_transcript(
            db_session,
            meeting_id=meeting,
            utterances=TRANSCRIPT[:1],
            duration_seconds=9.0,
            audio_deleted=True,
        )

        assert len(rows(db_session, meeting)) == 1

    def test_a_participant_keeps_what_the_pipeline_did_not_produce(
        self, db_session: Session, meeting: str
    ) -> None:
        """A rerun must not throw away somebody's answer to the S16 DM.

        `user_id`, `role` and `consented` come from a person confirming who they
        are. Replacing participant rows on every run would ask them again.
        """
        persist_transcript(
            db_session,
            meeting_id=meeting,
            utterances=TRANSCRIPT,
            duration_seconds=9.0,
            audio_deleted=True,
        )
        participant = db_session.scalar(
            sa.select(Participant).where(
                Participant.meeting_id == meeting,
                Participant.speaker_label == "SPEAKER_00",
            )
        )
        assert participant is not None
        participant.role = "PM"
        participant.consented = True
        db_session.flush()
        kept_id = participant.id

        persist_transcript(
            db_session,
            meeting_id=meeting,
            utterances=TRANSCRIPT,
            duration_seconds=9.0,
            audio_deleted=True,
        )

        again = db_session.get(Participant, kept_id)
        assert again is not None
        assert again.role == "PM"
        assert again.consented is True


class TestWhatItRefusesToWrite:
    def test_unmasked_text_is_refused(self, db_session: Session, meeting: str) -> None:
        """The same check `packages/integrations` runs on the way out.

        If it fires here, masking did not run — and the rest of the system would
        refuse to deliver what we were about to store.
        """
        with pytest.raises(PrivacyViolationError):
            persist_transcript(
                db_session,
                meeting_id=meeting,
                utterances=(spoken("SPEAKER_00", 0.0, 3.0, "제 번호는 010-1234-5678입니다"),),
                duration_seconds=3.0,
                audio_deleted=True,
            )

    def test_nothing_is_written_when_one_utterance_is_unmasked(
        self, db_session: Session, meeting: str
    ) -> None:
        """Checked before the first delete, so a refusal leaves the meeting alone."""
        persist_transcript(
            db_session,
            meeting_id=meeting,
            utterances=TRANSCRIPT,
            duration_seconds=9.0,
            audio_deleted=True,
        )
        with pytest.raises(PrivacyViolationError):
            persist_transcript(
                db_session,
                meeting_id=meeting,
                utterances=(*TRANSCRIPT, spoken("SPEAKER_02", 9.5, 12.0, "a@b.com 로 주세요")),
                duration_seconds=12.0,
                audio_deleted=True,
            )
        assert len(rows(db_session, meeting)) == 3

    def test_an_unknown_meeting_is_not_created(self, db_session: Session) -> None:
        with pytest.raises(NotFoundError):
            persist_transcript(
                db_session,
                meeting_id="mtg_does_not_exist",
                utterances=TRANSCRIPT,
                duration_seconds=9.0,
                audio_deleted=True,
            )


class TestTheFlagsConsumersRefuseOn:
    def test_the_meeting_records_what_the_filesystem_said(
        self, db_session: Session, meeting: str
    ) -> None:
        """`original_audio_deleted` is read from `Recording.deleted`, which is
        read from the filesystem rather than from having called unlink."""
        persist_transcript(
            db_session,
            meeting_id=meeting,
            utterances=TRANSCRIPT,
            duration_seconds=9.0,
            audio_deleted=False,
        )
        row = db_session.get(Meeting, meeting)
        assert row is not None
        assert row.original_audio_deleted is False
        assert row.pii_masked is True
        assert row.duration_seconds == 9.0
