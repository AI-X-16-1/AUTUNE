"""Write the transcript down. Module A is the only module that may.

`meetings`, `participants` and `utterances` live in ``packages/core`` and four
other modules read them. A writes them and nobody else does — invariant 4 — so
the shape of what lands here is a promise to B, C, D and E rather than a private
choice.

Two things this refuses to do, and both are refusals rather than assertions in a
docstring:

- **Write text that still contains personal data.** Checked with module A's own
  detector — the one that did the masking. `find_unmasked` in
  `packages/integrations` would be the obvious choice, and it is the wrong one:
  its patterns still end in `\b`, and a Hangul syllable is `\w`, so
  `010-1234-5678입니다` passes it clean (#126). A guard that cannot see the most
  ordinary shape in a Korean transcript is not a guard. Verifying with the same
  patterns that masked means the two cannot disagree.
- **Publish a transcript the decoder came apart in.** That is
  `quality.raise_if_collapsed`, called by the task before it gets here.

Running twice is safe. See ``persist_transcript``.

``transcript_payload`` builds the event from the rows this wrote, not from what
the pipeline computed. See it for why.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from autune_contracts.enums import TranscriptSource
from autune_contracts.transcript import (
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptReady,
)
from autune_contracts.transcript import Utterance as UtteranceContract
from autune_core import get_logger
from autune_core.entities import Meeting, Participant, Utterance
from autune_core.errors import NotFoundError, PrivacyViolationError

from .masking import mask
from .speakers import Utterance as SpokenUtterance

log = get_logger(__name__)


@dataclass(frozen=True)
class Persisted:
    """What the write produced. Counts only — the rows are meeting content."""

    meeting_id: str
    utterances: int
    participants: int

    def __repr__(self) -> str:
        return (
            f"Persisted(meeting={self.meeting_id}, utterances={self.utterances}, "
            f"participants={self.participants})"
        )


def persist_transcript(
    session: Session,
    *,
    meeting_id: str,
    utterances: tuple[SpokenUtterance, ...],
    duration_seconds: float,
    audio_deleted: bool,
) -> Persisted:
    """Replace this meeting's transcript with ``utterances``.

    **Replace, not append.** The task carries ``acks_late``, so a worker that
    dies after writing and before acknowledging gets the same recording again;
    appending would give the meeting two copies of itself and every consumer
    twice the utterances. Deleting this meeting's rows first makes a second run
    produce the same database as the first, which is the only definition of
    "safe to run twice" that survives a broker retry.

    Ids are generated rather than derived from the audio. A deterministic id
    would make the write an upsert, but the number of utterances changes between
    runs — a different glossary or a fallback pass cuts the speech differently —
    so ids from the second run would collide with some rows and orphan others.
    Replacing the set avoids reasoning about which.

    ``audio_deleted`` comes from ``storage.Recording.deleted``, which is read
    from the filesystem rather than from having called ``unlink``. It is what
    ``PrivacyFlags.original_audio_deleted`` is set from, and consumers refuse a
    transcript whose flag is False.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)

    for utterance in utterances:
        # Anything the masker still finds is something the masker did not run
        # over: a masked value contains "*" and matches no pattern.
        found = mask(utterance.text)
        if found.spans:
            categories = sorted(found.counts)
            # Never the text. This message reaches error tracking, which is a
            # third party -- privacy.md section 2.
            raise PrivacyViolationError(
                f"refusing to store an utterance still containing {categories}; "
                "masking did not run before the write",
                meeting_id=meeting_id,
            )

    session.execute(sa.delete(Utterance).where(Utterance.meeting_id == meeting_id))

    participants = _participants_for(session, meeting_id, utterances)
    session.add_all(
        Utterance(
            meeting_id=meeting_id,
            participant_id=participants[spoken.speaker].id,
            speaker_label=spoken.speaker,
            start_sec=spoken.start,
            end_sec=spoken.end,
            text=spoken.text,
            confidence=spoken.confidence,
        )
        for spoken in utterances
    )

    meeting.duration_seconds = duration_seconds
    meeting.original_audio_deleted = audio_deleted
    meeting.pii_masked = True

    session.flush()
    result = Persisted(
        meeting_id=meeting_id, utterances=len(utterances), participants=len(participants)
    )
    log.info(
        "transcript_persisted",
        meeting_id=meeting_id,
        utterances=result.utterances,
        participants=result.participants,
        audio_deleted=audio_deleted,
    )
    return result


def _participants_for(
    session: Session, meeting_id: str, utterances: tuple[SpokenUtterance, ...]
) -> dict[str, Participant]:
    """One participant row per distinct speaker label, reusing what is there.

    Reused rather than replaced because a participant carries things this
    pipeline did not produce: a ``user_id`` somebody confirmed from the S16 DM,
    a role, and ``consented``. A rerun that dropped the rows would throw away a
    person's answer and ask them again.

    A label the meeting has not seen gets a new row with ``consented=False``.
    False is the honest default — the pipeline heard a voice, and nobody has
    said yet whether that voice agreed to be here.
    """
    existing = {
        participant.speaker_label: participant
        for participant in session.scalars(
            sa.select(Participant).where(Participant.meeting_id == meeting_id)
        )
    }

    for label in dict.fromkeys(spoken.speaker for spoken in utterances):
        if label in existing:
            continue
        participant = Participant(meeting_id=meeting_id, speaker_label=label, consented=False)
        session.add(participant)
        existing[label] = participant

    session.flush()
    return existing


def transcript_payload(session: Session, *, meeting_id: str) -> TranscriptReady:
    """The ``TranscriptReady`` for a meeting, read back from what was written.

    **Built from the rows, not from the pipeline's output.** Three reasons, and
    the first two are correctness rather than taste:

    - ``Utterance.id`` is assigned at the write and is how a consumer points
      back at a row. Nothing upstream of the write knows it.
    - ``speaker_id`` and ``role`` live on ``Participant``, and
      ``_participants_for`` *reuses* those rows: a ``user_id`` somebody
      confirmed from an earlier meeting's DM is in the database and was never
      in this pipeline's output. Building the payload from the transcript would
      publish ``speaker_id=None`` for a person the system already knows.
    - It makes the event a statement about what is stored. Publish after the
      transaction commits and the two cannot disagree; publish from memory and
      a rollback leaves four modules processing a meeting that does not exist.

    ``participants`` is **speaker labels**, one per diarization label, in the
    order they first speak. The contract field carries no description and no
    consumer reads it yet, so this is a choice rather than a reading: labels are
    what ``Utterance.speaker`` already carries, they are what module D's fixture
    puts there, and they are the only value that is available for a meeting
    where nobody has been identified. It is not a list of people -- one person
    split across two clusters is two entries, which is #167's property arriving
    here. Filed as #183 to get the description into the contract.
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError("meeting", meeting_id)

    participants = {
        participant.id: participant
        for participant in session.scalars(
            sa.select(Participant).where(Participant.meeting_id == meeting_id)
        )
    }
    rows = list(
        session.scalars(
            sa.select(Utterance)
            .where(Utterance.meeting_id == meeting_id)
            # `id` breaks the tie: two utterances can start in the same
            # hundredth of a second, and an event whose order changes between
            # reads is one four consumers would each have to sort themselves.
            .order_by(Utterance.start_sec, Utterance.id)
        )
    )

    return TranscriptReady(
        meeting_id=meeting_id,
        utterances=[
            UtteranceContract(
                id=row.id,
                speaker=row.speaker_label,
                speaker_id=_participant_of(participants, row).user_id
                if row.participant_id
                else None,
                role=_participant_of(participants, row).role if row.participant_id else None,
                start=row.start_sec,
                end=row.end_sec,
                text=row.text,
                confidence=row.confidence,
            )
            for row in rows
        ],
        metadata=TranscriptMetadata(
            duration=meeting.duration_seconds or 0.0,
            participants=list(dict.fromkeys(row.speaker_label for row in rows)),
            source=TranscriptSource(meeting.source),
            language=meeting.language,
            privacy=PrivacyFlags(
                # Read back from the row rather than passed in. The flags are a
                # promise to four modules, and `require_privacy_guarantees`
                # makes them refuse the transcript when either is False; a
                # promise assembled from a local variable can be true of the
                # value and false of the database.
                original_audio_deleted=meeting.original_audio_deleted,
                pii_masked=meeting.pii_masked,
            ),
        ),
    )


def _participant_of(participants: dict[str, Participant], row: Utterance) -> Participant:
    """The participant a row points at, or a loud failure.

    A missing row means the foreign key was satisfied by a participant of
    another meeting, which nothing in this module can produce and which would
    otherwise publish somebody else's ``user_id`` against this meeting's speech.
    """
    participant = participants.get(str(row.participant_id))
    if participant is None:
        raise NotFoundError("participant", str(row.participant_id))
    return participant
