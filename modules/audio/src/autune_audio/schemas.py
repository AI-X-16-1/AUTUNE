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
from pydantic import BaseModel, Field

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
    """

    title: str
    original_audio_deleted: bool
    pii_masked: bool


class TeamSummary(BaseModel):
    """A team the caller may open a meeting for. Id and name; nothing else a
    browser needs to fill ``MeetingCreate.team_id``."""

    team_id: str
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
