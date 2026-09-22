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

from typing import Any

import numpy as np

from autune_audio.schemas import Waveform

CHECKPOINT = "pyannote/wespeaker-voxceleb-resnet34-LM"
MIN_SECONDS = 0.5
"""Audio shorter than this is zero-padded to it before inference. The
tracker, not the embedder, decides what a short utterance may do."""


class Embedder:
    def __init__(self, checkpoint: str = CHECKPOINT, token: str = "") -> None:
        self._checkpoint = checkpoint
        self._token = token
        self._inference: Any = None

    def warm_up(self) -> None:
        """Load the model. Raises when it cannot; the session decides what a
        channel without speaker labels does (it goes on)."""
        if self._inference is not None:
            return
        from pyannote.audio import Inference, Model  # noqa: PLC0415

        model = Model.from_pretrained(self._checkpoint, token=self._token or None)
        if model is None:
            raise RuntimeError(f"failed to load {self._checkpoint}")
        self._inference = Inference(model, window="whole")

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
        vector = np.asarray(raw, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            raise ValueError("the embedding has no direction")
        return vector / norm
