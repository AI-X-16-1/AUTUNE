"""Internal schemas for module A.

Anything another module needs belongs in ``packages/contracts``, not here. These
types describe the stages inside this pipeline: a decoded waveform, and what the
transcriber returns before speakers are attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, field_validator

SAMPLE_RATE = 16_000
"""What both Whisper and pyannote want. Decoding to it once means neither
resamples on its own, so the two timelines describe the same audio."""


@dataclass(frozen=True)
class Waveform:
    """Decoded audio, in memory. 16 kHz mono float32 in [-1, 1].

    This is raw audio. It is never written to disk and never logged; see
    docs/architecture/privacy.md section 1.
    """

    samples: np.ndarray
    sample_rate: int = SAMPLE_RATE

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate

    def __repr__(self) -> str:
        """Length and rate, never the samples — this is a recording."""
        return f"Waveform({self.duration:.1f}s @ {self.sample_rate}Hz)"


@dataclass(frozen=True)
class Word:
    """One word with its own start and end.

    Speaker alignment (#6) needs this granularity: a Whisper segment can span a
    turn change, and only word times say where to cut it.
    """

    start: float
    end: float
    text: str
    probability: float


@dataclass(frozen=True)
class Turn:
    """One speaker's stretch of speech.

    The same shape whether it came from a diarizer or from a label file, which
    is why ``eval.metrics`` scores DER against this type rather than its own — a
    reference and a hypothesis that are different types cannot be compared
    without a conversion nobody would think to check.

    ``speaker`` is a local label like ``SPEAKER_00``. It means "the same voice as
    the other turns with this label in this recording" and nothing more; putting
    a name to it is identification, which is a separate step.
    """

    start: float
    end: float
    speaker: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class Segment:
    """A stretch of speech Whisper emitted as one unit."""

    start: float
    end: float
    text: str
    words: tuple[Word, ...]


@dataclass(frozen=True)
class Transcription:
    """Everything the transcriber produced for one recording.

    Speaker labels are not here. They come from diarization and are joined in a
    later step, which is why nothing in this file mentions a speaker.
    """

    segments: tuple[Segment, ...]
    language: str
    language_probability: float
    duration: float

    @property
    def words(self) -> tuple[Word, ...]:
        return tuple(w for s in self.segments for w in s.words)

    def __repr__(self) -> str:
        """Counts, never text — a transcript is meeting content."""
        return (
            f"Transcription({len(self.segments)} segments, {len(self.words)} words, "
            f"{self.duration:.1f}s, lang={self.language})"
        )


# --- API request and response bodies ----------------------------------------
#
# The types above describe stages inside the pipeline; these describe the HTTP
# surface. Neither belongs in ``packages/contracts`` — a contract type is what
# another *module* consumes, and nothing here crosses that line.


class MeetingCreate(BaseModel):
    """What a client sends to open a meeting."""

    title: str = Field(min_length=1, max_length=400)
    team_id: str = Field(min_length=1, max_length=64)
    started_at: datetime | None = None
    """When the meeting began. Absent for a recording uploaded after the fact."""


class MeetingState(BaseModel):
    """The id and where the meeting has got to. Returned by both write routes.

    Deliberately thin. A meeting carries a title the team wrote and, once the
    pipeline has run, its transcript — none of which the caller of a write route
    needs echoed back, and all of which is meeting content. The screen polls or
    reads the meeting properly when it wants more than this.
    """

    meeting_id: str
    status: str


class MeetingDetail(MeetingState):
    """A meeting's own row, for the screen that follows it (S12, S15).

    The two flags are what the pipeline actually wrote: ``persist_transcript``
    sets them in the same transaction as the utterances, so a screen can say
    "original deleted" and "masked" from stored state rather than from having
    reached a stage in a diagram. Nothing derived from the transcript is here;
    that is ``/transcripts/{id}``.

    ``team_id`` is the meeting's own row, not anything derived from the
    transcript: the speaker picker (``GET /teams/{id}/members``) needs it and
    a screen that already has the meeting should not make a second call to
    learn who owns it.
    """

    title: str
    original_audio_deleted: bool
    pii_masked: bool
    team_id: str
    stage: str | None = None
    """The running attempt's step (``progress.STAGES``), while ``analyzing``.
    Null otherwise, and before the worker has picked the job up."""
    stage_progress: float | None = None
    """How far through ``stage``, 0..1."""


class MeetingSummary(BaseModel):
    """One row of the home screen's meeting list (S05).

    **Four fields, and the fourth is a date.** A list row needs a link target, a
    name, a state and a place in time; everything else it could show is either
    another module's or something invariant 11 does not let this route say.

    **No counts.** An utterance count is one join from a per-person speech
    volume, which privacy.md section 3 forbids surfacing to anybody but the
    speaker -- the line is drawn at the derived metric, not at the column, so a
    per-meeting total that a caller can difference against a participant list
    does not get a pass for being an aggregate. Action-item and gap counts are
    modules B's and C's rows, which A may not read (invariant 2). The row says
    what happened to the *meeting*; each one links to the screens that own the
    rest.

    ``started_at`` is nullable because a recording uploaded after the fact has
    no start time (``MeetingCreate``). The list still orders such a meeting by
    when its row was made -- see ``service.meetings_for`` -- but it does not
    invent a ``started_at`` to show for it, because a created-at printed as a
    meeting time is a wrong answer rather than a missing one.
    """

    meeting_id: str
    title: str
    status: str
    started_at: datetime | None


class TeamCreate(BaseModel):
    """S02: a workspace and the creator's job role. Nobody else is added.

    Invitations are not part of this: see ``service.create_team``. A body that
    still carries ``invite_emails`` is accepted and the field ignored, as
    Pydantic does with any unknown key.
    """

    name: str = Field(min_length=2, max_length=40)
    """2-40 characters after trimming, the limit S02 states."""
    role: str | None = Field(default=None, min_length=1, max_length=50)
    """The creator's job role — PM, Backend, Design... Stored on their
    membership and used for role-level analytics only."""

    @field_validator("name", mode="before")
    @classmethod
    def _trim_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class TeamSummary(BaseModel):
    """A team the caller may open a meeting for. Id and name; nothing else a
    browser needs to fill ``MeetingCreate.team_id``."""

    team_id: str
    name: str


class SpeakerCandidate(BaseModel):
    """Who a voice might be. A suggestion: nothing is written until somebody
    confirms it (``POST /meetings/{id}/speakers/{label}``)."""

    user_id: str
    name: str
    similarity: float


class SpeakerEntry(BaseModel):
    """One speaker label of one meeting.

    No utterance count and no duration, on purpose: in a four-person meeting a
    per-speaker count is a per-person speech volume, which privacy.md section 3
    forbids -- the same reason ``UnidentifiedSpeaker.tsx`` dropped "발화 41건".
    """

    speaker_label: str
    user_id: str | None
    candidate: SpeakerCandidate | None


class SpeakerAssignment(BaseModel):
    """ "``화자 2`` is this person." The body of the confirmation."""

    user_id: str


class TeamMemberSummary(BaseModel):
    """A person the picker can offer. Id and name; a browser needs no more."""

    user_id: str
    name: str


class ConsentAttestation(BaseModel):
    """What a member sends to say everyone in the recording consented.

    ``Literal[True]`` rather than ``bool``: ``false`` is not a revocation and
    not a no-op, it is a request this route has no meaning for, and 422 says so.
    Revocation is S10/S11 (#190).
    """

    attested: Literal[True]


class ConsentState(BaseModel):
    meeting_id: str
    attested: bool


class TeamPrivacy(BaseModel):
    """S29's workspace half: what the team has chosen, for one team."""

    team_id: str
    retention_days: int


class TeamPrivacyUpdate(BaseModel):
    """The windows S29 offers. ``teams.retention_days`` takes any integer; the
    route takes only these, so the screen and the column cannot disagree."""

    retention_days: Literal[30, 90, 180, 365]


class MyData(BaseModel):
    """S29's "내 데이터": what Autune holds about the caller, as counts.

    Only ever about the caller -- every number is computed with
    ``user_id == caller`` -- so nothing here is one person's data shown to
    another, and it carries no speaking ratio (privacy.md section 3: that goes
    to the speaker by DM and is not stored, so there is nothing to count).
    """

    meetings_with_my_speech: int
    voice_profile_rows: int
    voice_profile_since: datetime | None
    consents_attested: int


class SpeechDeleted(BaseModel):
    """What "내 발화 데이터 모두 삭제" removed."""

    utterances: int
    voice_rows: int


class AccountDeleted(BaseModel):
    """What account deletion removed of the caller's speech.

    A body rather than 204: the shared web client parses every success as
    JSON (#359), and a count is something the screen can say.
    """

    utterances: int


class PiiReport(BaseModel):
    """S30: which characters of an utterance are personal data the masker missed.

    Offsets, never the text: the span is read from the stored row, so the
    unmasked string is not in the request (``pii_report``).
    """

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    category: Literal["name", "internal_id", "contact", "other"]
    include_similar: bool = True


class PiiReported(BaseModel):
    utterances: int
    occurrences: int
    republished: bool
    """Whether B, C and D were sent the corrected transcript. False when the
    meeting was never announced (still being transcribed), or the publish failed."""
