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

from autune_audio.persistence import persist_transcript, transcript_payload
from autune_audio.speakers import Utterance as SpokenUtterance
from autune_contracts.enums import TranscriptSource
from autune_contracts.transcript import TranscriptReady
from autune_core.entities import Meeting, Participant, Utterance
from autune_core.errors import NotFoundError, PrivacyViolationError


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


class TestThePayloadFourModulesRead:
    """`transcript_payload` is read back from the rows, not from the pipeline."""

    def write(self, session: Session, meeting_id: str, *, deleted: bool = True) -> None:
        persist_transcript(
            session,
            meeting_id=meeting_id,
            utterances=TRANSCRIPT,
            duration_seconds=9.0,
            audio_deleted=deleted,
        )

    def test_it_validates_as_the_contract_and_passes_the_consumer_guard(
        self, db_session: Session, meeting: str
    ) -> None:
        """The completion criterion of #9: B, C and D parse a real payload.

        Round-tripped through `model_dump(mode="json")` because that is what
        goes on the wire -- a payload that only validates as objects is one the
        broker has never carried.
        """
        self.write(db_session, meeting)
        payload = transcript_payload(db_session, meeting_id=meeting)

        again = TranscriptReady.model_validate(payload.model_dump(mode="json"))
        again.require_privacy_guarantees()

        assert again.meeting_id == meeting
        assert [u.text for u in again.utterances] == [u.text for u in TRANSCRIPT]
        assert again.metadata.duration == 9.0
        assert again.metadata.source is TranscriptSource.FILE_UPLOAD
        assert again.metadata.language == "ko"

    def test_every_utterance_carries_the_id_of_the_row_it_came_from(
        self, db_session: Session, meeting: str
    ) -> None:
        """The id is assigned at the write; nothing upstream of it knows one.

        It is also how a consumer points back at a row, so a generated-per-call
        id would be worse than no id at all.
        """
        self.write(db_session, meeting)
        payload = transcript_payload(db_session, meeting_id=meeting)
        assert [u.id for u in payload.utterances] == [r.id for r in rows(db_session, meeting)]

    def test_a_speaker_somebody_identified_earlier_is_in_the_payload(
        self, db_session: Session, meeting: str
    ) -> None:
        """Why the payload is built from the rows rather than the transcript.

        `_participants_for` reuses participant rows, so a `user_id` confirmed
        from an earlier DM is in the database and was never in this pipeline's
        output. Built from the transcript, this publishes `speaker_id=None` for
        somebody the system already knows.
        """
        from autune_core import User

        user = User(email="hong@example.com", display_name="홍길동")
        db_session.add(user)
        db_session.flush()

        self.write(db_session, meeting)
        participant = db_session.scalar(
            sa.select(Participant).where(
                Participant.meeting_id == meeting,
                Participant.speaker_label == "SPEAKER_00",
            )
        )
        assert participant is not None
        participant.user_id = user.id
        participant.role = "PM"
        db_session.flush()

        self.write(db_session, meeting)  # a rerun, as acks_late allows
        payload = transcript_payload(db_session, meeting_id=meeting)

        identified = [u for u in payload.utterances if u.speaker == "SPEAKER_00"]
        assert [u.speaker_id for u in identified] == [user.id, user.id]
        assert [u.role for u in identified] == ["PM", "PM"]
        assert [u.speaker_id for u in payload.utterances if u.speaker == "SPEAKER_01"] == [None]

    def test_utterances_come_out_in_the_order_they_were_said(
        self, db_session: Session, meeting: str
    ) -> None:
        """Ordered here so four consumers do not each have to sort."""
        self.write(db_session, meeting)
        payload = transcript_payload(db_session, meeting_id=meeting)
        starts = [u.start for u in payload.utterances]
        assert starts == sorted(starts)

    def test_participants_are_speaker_labels_in_first_speaking_order(
        self, db_session: Session, meeting: str
    ) -> None:
        """One entry per diarization label, not per person -- see #183.

        SPEAKER_00 speaks twice and appears once; the list is labels rather
        than people, which is the only value available for a meeting where
        nobody has been identified.
        """
        self.write(db_session, meeting)
        payload = transcript_payload(db_session, meeting_id=meeting)
        assert payload.metadata.participants == ["SPEAKER_00", "SPEAKER_01"]

    def test_the_privacy_flags_are_read_back_from_the_meeting_row(
        self, db_session: Session, meeting: str
    ) -> None:
        """A promise assembled from a local variable can be true of the value
        and false of the database. Consumers refuse on these."""
        self.write(db_session, meeting, deleted=False)
        payload = transcript_payload(db_session, meeting_id=meeting)

        assert payload.metadata.privacy.original_audio_deleted is False
        with pytest.raises(ValueError, match="raw audio was not deleted"):
            payload.require_privacy_guarantees()

    def test_an_unknown_meeting_has_no_payload(self, db_session: Session) -> None:
        with pytest.raises(NotFoundError):
            transcript_payload(db_session, meeting_id="mtg_does_not_exist")
