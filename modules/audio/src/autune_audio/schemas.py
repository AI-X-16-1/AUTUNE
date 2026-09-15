"""Internal schemas for module A.

Anything another module needs belongs in ``packages/contracts``, not here. These
types describe the stages inside this pipeline — a decoded waveform, and what
the transcriber returns before speakers are attached — and, at the end of the
file, what this module's own routes answer with.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel

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


# --- HTTP ------------------------------------------------------------------ #
# What the router returns. Everything above describes a stage inside the
# pipeline; this describes an answer to a request.


class RecordingAccepted(BaseModel):
    """The meeting a recording was queued against.

    Returned with 202, not 200: the recording has been taken and the work has
    been queued, and nothing about the transcript exists yet. The caller polls
    the meeting rather than waiting — transcription runs for minutes.

    There is no upload path in here and there never should be. The path is where
    a recording sits until the worker deletes it, and it is not something a
    client has any business knowing.
    """

    meeting_id: str
    status: str
