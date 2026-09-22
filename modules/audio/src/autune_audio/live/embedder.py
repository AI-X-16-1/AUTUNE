"""One utterance's voice as a vector.

The embedding model inside ``pyannote/speaker-diarization-3.1`` -- the stored
path's diarizer -- so the vector lives in the same space the stored path will
use for identification (#6) and the checkpoint is already on disk. It is
public on the hub; the token is passed through so a fresh machine can fetch
it.

``pyannote.audio`` and ``torch`` are imported inside the methods that need
them, the way ``diarization.PyannoteDiarizer`` does: ``apps/api`` must not
load torch to answer a health check, and the unit tests fake both.

The audio is the utterance the segmenter cut; it is never written anywhere
and this module never logs. Design: ``docs/modules/audio-live-speakers.md``
section 3.1.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

from autune_audio.live.speakers import unit
from autune_audio.schemas import Waveform

CHECKPOINT = "pyannote/wespeaker-voxceleb-resnet34-LM"
MIN_SECONDS = 0.5
"""Audio shorter than this is zero-padded to it before inference. The
tracker, not the embedder, decides what a short utterance may do."""


class EmbedderUnavailable(RuntimeError):  # noqa: N818 - name fixed by the task interface
    """The model failed to load once; every later call answers this at once.
    Carries the original exception's type only."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class Embedder:
    def __init__(self, checkpoint: str = CHECKPOINT, token: str = "") -> None:
        self._checkpoint = checkpoint
        self._token = token
        self._inference: Any = None
        self._unavailable: str | None = None
        self.lock = threading.Lock()
        """Serialises calls into the model. Separate from the transcriber's
        lock on purpose (see ``transcriber.off_loop``)."""

    def warm_up(self) -> None:
        """Load the model. Raises when it cannot; the session decides what a
        channel without speaker labels does (it goes on).

        A failed load is remembered: every call after the first raises
        ``EmbedderUnavailable`` at once rather than retrying a download or an
        import that will not succeed on this machine this process."""
        if self._inference is not None:
            return
        if self._unavailable is not None:
            raise EmbedderUnavailable(self._unavailable)
        try:
            from pyannote.audio import Inference, Model  # noqa: PLC0415

            model = Model.from_pretrained(self._checkpoint, token=self._token or None)
            if model is None:
                raise RuntimeError(f"failed to load {self._checkpoint}")
            self._inference = Inference(model, window="whole")
        except Exception as exc:
            # Remembered per process: a machine with no extra, no token or
            # no network does not re-discover that on every connection,
            # holding a lock through a download timeout each time.
            self._unavailable = type(exc).__name__
            raise

    def embed(self, waveform: Waveform) -> np.ndarray:
        """A unit-length float32 vector for the voice in ``waveform``."""
        import torch  # noqa: PLC0415

        self.warm_up()
        samples = np.asarray(waveform.samples, dtype=np.float32)
        floor = int(MIN_SECONDS * waveform.sample_rate)
        if len(samples) < floor:
            samples = np.pad(samples, (0, floor - len(samples)))
        raw = self._inference(
            {
                "waveform": torch.from_numpy(samples).unsqueeze(0),
                "sample_rate": waveform.sample_rate,
            }
        )
        return unit(np.asarray(raw, dtype=np.float32))
