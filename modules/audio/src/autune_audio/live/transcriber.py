"""Transcribe one segment at a time, off the event loop.

The seam. Live transcription runs in the API process today (design, section
1); if it moves to a worker later -- after #258, when concurrent meetings
exist -- the body of ``run`` becomes "send to the worker and await the result"
and nothing else in ``live/`` changes.

The live path loads its own model and its own thread count (the ``live_*``
settings, `autune_audio.config`) rather than the stored path's: on the
reference laptop a row costs about 2.5 s regardless of utterance length,
because the encoder pads every segment to a thirty-second window.

Two properties:

- **One lock per process.** Segments that arrive while one is being
  transcribed wait their turn. Two meetings live at once share the lock; once
  their combined load passes real time the delay grows for the rest of the
  meeting rather than doubling -- an MVP limit, stated in audio.md.
- **A thread, not the loop.** ``anyio.to_thread`` keeps the socket handler
  reading frames and other connections served while Whisper works.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import anyio

from autune_audio.live import backends
from autune_audio.schemas import Transcription, Waveform
from autune_core import get_logger

log = get_logger(__name__)

_LOCK = threading.Lock()


async def off_loop[T](fn: Callable[[], T], *, lock: threading.Lock = _LOCK) -> T:
    """Run ``fn`` on a worker thread under ``lock`` -- the transcriber's own
    by default. The embedder passes its own: the two models share no state,
    and a slow embedding must not hold up another meeting's decode."""

    def guarded() -> T:
        with lock:
            return fn()

    return await anyio.to_thread.run_sync(guarded)


class Transcriber:
    def __init__(
        self,
        *,
        transcribe: Callable[[Waveform], Transcription] | None = None,
        warm_up: Callable[[], None] | None = None,
    ) -> None:
        if transcribe is None or warm_up is None:
            default_transcribe, default_warm_up = backends.select()
            transcribe = transcribe or default_transcribe
            warm_up = warm_up or default_warm_up
        self._transcribe = transcribe
        self._warm_up = warm_up
        self._warm = False

    async def warm_up(self) -> None:
        """Load the model. A failure propagates and leaves ``warm`` False, so
        the next connection tries again rather than assuming.

        Under the same lock as ``run``: two cold connections arriving
        together must not each build a model, so the second waits for the
        first and then finds the work done."""
        if self._warm:
            return

        def guarded() -> None:
            with _LOCK:
                if self._warm:
                    return
                self._warm_up()
                self._warm = True

        await anyio.to_thread.run_sync(guarded)

    async def run(self, waveform: Waveform) -> Transcription:
        def decode() -> Transcription:
            started = time.monotonic()
            transcription = self._transcribe(waveform)
            # Audio length in, decode time out: the number a "why is it
            # slow" question needs, and nothing that is in the audio.
            log.info(
                "live_decode",
                audio_s=round(waveform.duration, 1),
                decode_s=round(time.monotonic() - started, 2),
            )
            return transcription

        return await off_loop(decode)
