"""One connection, one session: frames in, masked rows out.

The unmasked transcription is a local variable in ``_row`` and is dead by the
time the row exists. It is not logged, not kept on the object and not in any
exception: ``TranscribeFailed`` carries the exception *type* of what went
wrong and nothing else, because ffmpeg-style errors can quote what they were
reading. It is raised with its ``__cause__`` cut (``from None``), so nothing
downstream -- a traceback, ``logger.exception``, an error tracker -- prints
the original exception's message either. The speaker embedding is taken from
the audio before masking and never from the text; the vector is a local here
and a running mean in the tracker, nothing more.
"""

from __future__ import annotations

from typing import Literal

import numpy as np

from autune_audio.config import get_settings
from autune_audio.live.embedder import Embedder
from autune_audio.live.segmenter import Segment, Segmenter
from autune_audio.live.speakers import SpeakerTracker
from autune_audio.live.transcriber import Transcriber, off_loop
from autune_audio.masking import EntityRecogniser, mask
from autune_audio.recognition import get_recogniser
from autune_contracts.transcript import Utterance
from autune_core import get_logger
from autune_core.ids import new_id

log = get_logger(__name__)

NO_SPEAKER = "?"
"""The label of a row when the session has no working embedder. A cluster
label is ``화자 N`` (``live.speakers``); ``TranscriptRow`` keys "unidentified"
on ``speaker_id`` being null, not on either string."""


class TranscribeFailed(RuntimeError):  # noqa: N818 - name fixed by the design doc and the route
    """A segment could not be transcribed. Carries the cause's type only."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__(type(cause).__name__)
        self.kind = type(cause).__name__


class LiveSession:
    def __init__(
        self,
        *,
        segmenter: Segmenter,
        transcriber: Transcriber,
        recogniser: EntityRecogniser | None = None,
        min_confidence: float | None = None,
        embedder: Embedder | None = None,
        tracker: SpeakerTracker | None = None,
    ) -> None:
        self._segmenter = segmenter
        self._transcriber = transcriber
        # The same recogniser the stored path uses, chosen by the same setting.
        self._recogniser = recogniser if recogniser is not None else get_recogniser()
        settings = get_settings()
        self._min_confidence = (
            min_confidence if min_confidence is not None else settings.live_min_confidence
        )
        # None is the degraded mode: every row is NO_SPEAKER. The route passes
        # the process-wide embedder; a session whose embedder fails sets this
        # back to None and goes on without labels.
        self._embedder = embedder
        self._tracker = tracker or SpeakerTracker(
            threshold=settings.live_speaker_threshold,
            min_seconds=settings.live_speaker_min_s,
        )
        self.state: Literal["recording", "paused", "ended"] = "recording"
        self.rows_sent = 0

    @property
    def tracker(self) -> SpeakerTracker:
        return self._tracker

    async def on_frame(self, pcm16: bytes) -> list[Utterance]:
        """One binary frame from the browser: PCM16, mono, 16 kHz."""
        if self.state != "recording":
            return []
        # A stray odd-length frame is truncated, not raised on: one bad frame
        # from the browser must not take the socket down.
        pcm16 = pcm16[: len(pcm16) - len(pcm16) % 2]
        frame = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        rows = []
        for segment in self._segmenter.feed(frame):
            row = await self._row(segment)
            if row is not None:
                rows.append(row)
        return rows

    def pause(self) -> None:
        if self.state == "recording":
            self.state = "paused"

    def resume(self) -> None:
        if self.state == "paused":
            self.state = "recording"

    async def stop(self) -> list[Utterance]:
        """Close the open utterance, if any, and end."""
        self.state = "ended"
        last = self._segmenter.flush()
        if last is None:
            return []
        row = await self._row(last)
        return [row] if row is not None else []

    async def warm_up(self) -> None:
        """Load the models before the browser is told the channel is ready.

        The transcriber's failure propagates -- no transcriber, no channel
        (the route answers 4503). The embedder's does not: a channel with no
        speaker labels is still a live transcript."""
        await self._transcriber.warm_up()
        if self._embedder is None:
            return
        try:
            await off_loop(self._embedder.warm_up)
        except Exception as exc:
            log.warning("live_speaker_unavailable", reason=type(exc).__name__)
            self._embedder = None

    async def _row(self, segment: Segment) -> Utterance | None:
        """One segment through the model and the mask. ``None`` when the model
        heard nothing in it -- a breath the VAD took for speech is not a row."""
        try:
            transcription = await self._transcriber.run(segment.waveform)
        except Exception as exc:
            log.warning("live_segment_failed", error=type(exc).__name__)
            raise TranscribeFailed(exc) from None

        spoken = " ".join(s.text.strip() for s in transcription.segments).strip()
        words = transcription.words
        confidence = float(np.mean([w.probability for w in words])) if words else 0.0
        if not spoken:
            del spoken
            return None
        if confidence < self._min_confidence:
            # A fragment Whisper was guessing at -- the hallucinated rows of
            # the first microphone runs sat at 0.1-0.25 while real speech sat
            # above 0.5. Nothing is lost: the stored path remakes every row.
            # Dropped before labelling: a guess must not open a cluster.
            del spoken
            log.info(
                "live_segment_below_confidence",
                seconds=round(segment.end - segment.start, 1),
                confidence=round(confidence, 2),
            )
            return None

        speaker = await self._label(segment)
        masked = mask(spoken, recogniser=self._recogniser).text
        del spoken  # the unmasked string ends here

        self.rows_sent += 1
        log.info(
            "live_segment_transcribed",
            seconds=round(segment.end - segment.start, 1),
            chars=len(masked),
        )
        return Utterance(
            id=new_id("utt_live"),
            speaker=speaker,
            speaker_id=None,
            role=None,
            start=round(segment.start, 2),
            end=round(segment.end, 2),
            text=masked,
            confidence=max(0.0, min(1.0, confidence)),
        )

    async def _label(self, segment: Segment) -> str:
        """``화자 N`` from the voice, or ``NO_SPEAKER`` when there is no
        embedder. The first failure switches the embedder off for the
        session: paying the cost on every row would buy the same answer, and
        the exception type is all the log gets -- pyannote errors can quote
        paths."""
        if self._embedder is None:
            return NO_SPEAKER
        embedder = self._embedder

        def embed() -> np.ndarray:
            return embedder.embed(segment.waveform)

        try:
            vector = await off_loop(embed)
        except Exception as exc:
            log.warning("live_speaker_failed", error=type(exc).__name__)
            self._embedder = None
            return NO_SPEAKER
        return self._tracker.label(vector, segment.end - segment.start)
