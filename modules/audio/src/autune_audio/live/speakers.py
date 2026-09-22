"""Which live rows share a voice. A cluster, not a person.

One embedding per utterance comes in; a label ``화자 N`` goes out. The label is
the index of the nearest cluster when the cosine similarity clears the
threshold, and a new cluster otherwise. Numbers are given in order of first
appearance and never change: S13 is built on rows that do not move, and the
stored path's whole-file diarization corrects an over-split after the upload.

Pure numpy. Nothing here loads a model, and the vectors it holds -- one
running-mean centroid per cluster -- live in the session object and die with
the socket. An embedding is biometric data; nothing is logged but the cluster
number and the similarity. At info level, a per-row cluster id sitting next to
the row's duration in the surrounding logs would let a log reconstruct
per-cluster speaking time, which ``privacy.md`` section 3 forbids once a
cluster is a person; so ``live_speaker_labelled`` is logged at debug. Design:
``docs/modules/audio-live-speakers.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from autune_audio.config import AudioSettings
from autune_audio.speakers import UNIDENTIFIED
from autune_core import get_logger

log = get_logger(__name__)


@dataclass
class Cluster:
    centroid: np.ndarray
    """Unit length. The running mean of every vector that joined."""
    count: int


def _unit(vector: np.ndarray) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(v))
    if norm == 0.0:
        raise ValueError("a zero vector has no direction")
    return v / norm


def speaker_cap(settings: AudioSettings) -> int | None:
    """The same hint the stored path gives pyannote (#325): an exact count
    wins, else an upper bound, else nothing."""
    return settings.diarization_num_speakers or settings.diarization_max_speakers


class SpeakerTracker:
    """Nearest-centroid clustering with one threshold.

    The rules, in the order ``label`` applies them:

    1. The first utterance opens ``화자 1`` whatever its length.
    2. Score every centroid; ``best`` is the highest.
    3. An utterance shorter than ``min_seconds`` goes to ``best`` and moves
       nothing -- a sub-second embedding is unreliable, and without this every
       "네" would be a new speaker.
    4. ``similarity >= threshold`` joins ``best`` and moves its centroid.
    5. With ``max_speakers`` clusters already open, the utterance joins
       ``best`` even below the threshold: a room that knows it has N people
       does not get an (N+1)th label.
    6. Otherwise a new cluster opens.
    """

    def __init__(
        self,
        *,
        threshold: float,
        min_seconds: float = 1.0,
        max_speakers: int | None = None,
    ) -> None:
        if max_speakers is not None and max_speakers < 1:
            raise ValueError("max_speakers must be at least 1")
        self._threshold = threshold
        self._min_seconds = min_seconds
        self._max_speakers = max_speakers
        self._clusters: list[Cluster] = []

    @property
    def clusters(self) -> int:
        return len(self._clusters)

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def max_speakers(self) -> int | None:
        return self._max_speakers

    def label(self, vector: np.ndarray, seconds: float) -> str:
        v = _unit(vector)
        if not self._clusters:
            return self._open(v)

        scores = [float(c.centroid @ v) for c in self._clusters]
        best = int(np.argmax(scores))
        similarity = scores[best]

        if seconds < self._min_seconds:
            return self._assign(best, v, similarity, update=False)
        capped = self._max_speakers is not None and len(self._clusters) >= self._max_speakers
        if similarity >= self._threshold or capped:
            return self._assign(best, v, similarity, update=True)
        return self._open(v, similarity=similarity)

    def _assign(self, index: int, v: np.ndarray, similarity: float, *, update: bool) -> str:
        cluster = self._clusters[index]
        if update:
            cluster.centroid = _unit(cluster.centroid * cluster.count + v)
            cluster.count += 1
        log.debug(
            "live_speaker_labelled",
            cluster=index + 1,
            similarity=round(similarity, 3),
            opened=False,
        )
        return self._name(index)

    def _open(self, v: np.ndarray, *, similarity: float | None = None) -> str:
        self._clusters.append(Cluster(centroid=v, count=1))
        index = len(self._clusters) - 1
        log.debug(
            "live_speaker_labelled",
            cluster=index + 1,
            similarity=None if similarity is None else round(similarity, 3),
            opened=True,
        )
        return self._name(index)

    @staticmethod
    def _name(index: int) -> str:
        return f"{UNIDENTIFIED} {index + 1}"
