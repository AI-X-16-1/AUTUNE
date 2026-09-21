"""One connection, one session: frames in, masked rows out.

The unmasked transcription is a local variable in ``_row`` and is dead by the
time the row exists. It is not logged, not kept on the object and not in any
exception: ``TranscribeFailed`` carries the exception *type* of what went
wrong and nothing else, because ffmpeg-style errors can quote what they were
reading. It is raised with its ``__cause__`` cut (``from None``), so nothing
downstream -- a traceback, ``logger.exception``, an error tracker -- prints
the original exception's message either.
"""

from __future__ import annotations

from typing import Literal

import numpy as np

from autune_audio.live.segmenter import Segment, Segmenter
from autune_audio.live.transcriber import Transcriber
from autune_audio.masking import EntityRecogniser, mask
from autune_audio.recognition import get_recogniser
from autune_contracts.transcript import Utterance
from autune_core import get_logger
from autune_core.ids import new_id

log = get_logger(__name__)

UNIDENTIFIED = "?"
"""The display label of a live row. ``TranscriptRow`` keys "unidentified" on
``speaker_id`` being null, not on this string."""


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
    ) -> None:
        self._segmenter = segmenter
        self._transcriber = transcriber
        # The same recogniser the stored path uses, chosen by the same setting.
        self._recogniser = recogniser if recogniser is not None else get_recogniser()
        self.state: Literal["recording", "paused", "ended"] = "recording"
        self.rows_sent = 0

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
            rows.append(await self._row(segment))
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
        return [await self._row(last)] if last is not None else []

    async def warm_up(self) -> None:
        """Load the model before the browser is told the channel is ready."""
        await self._transcriber.warm_up()

    async def _row(self, segment: Segment) -> Utterance:
        try:
            transcription = await self._transcriber.run(segment.waveform)
        except Exception as exc:
            log.warning("live_segment_failed", error=type(exc).__name__)
            raise TranscribeFailed(exc) from None

        spoken = " ".join(s.text.strip() for s in transcription.segments).strip()
        words = transcription.words
        confidence = float(np.mean([w.probability for w in words])) if words else 0.0
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
            speaker=UNIDENTIFIED,
            speaker_id=None,
            role=None,
            start=round(segment.start, 2),
            end=round(segment.end, 2),
            text=masked,
            confidence=max(0.0, min(1.0, confidence)),
        )
