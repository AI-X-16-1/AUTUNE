"""Utterance boundaries on top of a voice-activity probability.

Frames come in at a fixed size; the segmenter says when an utterance has
ended. Three rules, from the design (section 3.1):

- speech of at least ``min_speech_ms`` followed by silence of at least
  ``min_silence_ms`` is a segment;
- a segment reaching ``max_segment_s`` is cut there (Whisper's window);
- frames with no speech in them are dropped, so silence never accumulates.

Timestamps are counted in frames from the start of the session, never read
from a clock: a slow transcribe must not move the next row.

The probability function is injected. The unit tests pass signal energy and
need no model; ``session.py`` passes silero's.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from autune_audio.schemas import SAMPLE_RATE, Waveform


@dataclass(frozen=True)
class Segment:
    """One utterance's audio and where it sat in the session."""

    waveform: Waveform
    start: float
    end: float


VAD_CONTEXT_S = 0.6
"""How much preceding audio the VAD sees along with the frame it scores.

silero is a recurrent model; faster-whisper's wrapper resets its state on
every call, so a 200 ms frame scored alone is scored with no memory of the
voice it is in the middle of. On a quiet or muffled microphone that read
soft syllables as silence and cut sentences into fragments Whisper then
hallucinated on (the first real-microphone runs). The frame is scored at the
end of a short window instead, and only the window's tail counts.
"""


def _silero_probability(audio: np.ndarray, tail: int) -> float:
    """The VAD faster-whisper ships, on ``audio`` whose last ``tail`` samples
    are the frame being scored. Loaded on first use, once per process."""
    from faster_whisper.vad import get_vad_model

    model = get_vad_model()
    usable = len(audio) - len(audio) % 512
    if usable == 0:
        return 0.0
    probabilities = model(audio[len(audio) - usable :])
    # One probability per 512-sample chunk; the frame is the last few chunks.
    chunks_in_tail = max(1, int(np.ceil(tail / 512)))
    return float(np.max(probabilities[-chunks_in_tail:]))


class Segmenter:
    def __init__(
        self,
        *,
        speech_probability: Callable[[np.ndarray], float] | None = None,
        min_speech_ms: int = 300,
        min_silence_ms: int = 700,
        max_segment_s: float = 30.0,
        threshold: float = 0.5,
    ) -> None:
        self._probability = speech_probability
        self._min_speech = min_speech_ms / 1000
        self._min_silence = min_silence_ms / 1000
        self._max_segment = max_segment_s
        self._threshold = threshold
        self._recent: list[np.ndarray] = []  # the VAD's context window, newest last
        self._elapsed = 0.0  # seconds of audio fed so far
        self._open: list[np.ndarray] = []  # frames of the utterance in progress
        self._open_start = 0.0
        self._speech_in_open = 0.0
        self._silence_run = 0.0

    def feed(self, frame: np.ndarray) -> list[Segment]:
        """One frame of float32 samples at ``SAMPLE_RATE``. Returns the
        segments it closed -- usually none, sometimes one."""
        duration = len(frame) / SAMPLE_RATE
        speech = self._speech_probability(frame) >= self._threshold
        closed: list[Segment] = []

        if speech:
            if not self._open:
                self._open_start = self._elapsed
                self._speech_in_open = 0.0
            self._open.append(frame)
            self._speech_in_open += duration
            self._silence_run = 0.0
        elif self._open:
            # Silence inside an utterance is kept: a pause between words is
            # part of it until it is long enough to end it.
            self._open.append(frame)
            self._silence_run += duration
            if self._silence_run >= self._min_silence:
                closed.extend(self._close())
        # A silent frame with nothing open is dropped here, on the floor.

        self._elapsed += duration

        if self._open and (self._elapsed - self._open_start) >= self._max_segment:
            closed.extend(self._close())
        return closed

    def _speech_probability(self, frame: np.ndarray) -> float:
        if self._probability is not None:
            return self._probability(frame)
        self._recent.append(frame)
        keep = int(VAD_CONTEXT_S * SAMPLE_RATE)
        while sum(len(f) for f in self._recent[:-1]) > keep:
            self._recent.pop(0)
        return _silero_probability(np.concatenate(self._recent), tail=len(frame))

    def flush(self) -> Segment | None:
        """Close whatever is open. Called on stop."""
        closed = self._close()
        return closed[0] if closed else None

    def _close(self) -> list[Segment]:
        frames, self._open = self._open, []
        silence_run, self._silence_run = self._silence_run, 0.0
        if not frames or self._speech_in_open < self._min_speech:
            return []
        samples = np.concatenate(frames)
        # The trailing silence that ended the utterance is not part of it.
        keep = len(samples) - int(silence_run * SAMPLE_RATE)
        samples = samples[: max(keep, 1)]
        end = self._open_start + len(samples) / SAMPLE_RATE
        return [
            Segment(
                waveform=Waveform(samples=samples, sample_rate=SAMPLE_RATE),
                start=self._open_start,
                end=end,
            )
        ]
