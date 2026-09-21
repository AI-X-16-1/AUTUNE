"""Transcribe one segment at a time, off the event loop.

The seam. Live transcription runs in the API process today (design, section
1); if it moves to a worker later -- after #258, when concurrent meetings
exist -- the body of ``run`` becomes "send to the worker and await the result"
and nothing else in ``live/`` changes.

Two properties, both about a CPU that transcribes at 0.73x real time:

- **One lock per process.** Segments that arrive while one is being
  transcribed wait their turn. Two meetings live at once share the lock and
  each sees roughly double the delay -- an MVP limit, stated in audio.md.
- **A thread, not the loop.** ``anyio.to_thread`` keeps the socket handler
  reading frames and other connections served while Whisper works.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import anyio

from autune_audio import pipeline
from autune_audio.glossary import build_prompt
from autune_audio.schemas import Transcription, Waveform

_LOCK = threading.Lock()


def _default_transcribe(waveform: Waveform) -> Transcription:
    return pipeline.transcribe(waveform, glossary=build_prompt())


class Transcriber:
    def __init__(
        self,
        *,
        transcribe: Callable[[Waveform], Transcription] | None = None,
        warm_up: Callable[[], None] | None = None,
    ) -> None:
        self._transcribe = transcribe or _default_transcribe
        self._warm_up = warm_up or pipeline.warm_up
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
        def guarded() -> Transcription:
            with _LOCK:
                return self._transcribe(waveform)

        return await anyio.to_thread.run_sync(guarded)
