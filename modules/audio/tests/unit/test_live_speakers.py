"""Which live rows share a voice: the label rules, with vectors and no model.

Vectors are unit basis vectors and small tilts of them, so the cosine
similarities are known by construction: e1 · e1 = 1, e1 · e2 = 0,
e1 · tilt(e1, e2, 0.1) ≈ 0.995.
"""

from __future__ import annotations

import numpy as np

from autune_audio.config import AudioSettings
from autune_audio.live.speakers import SpeakerTracker, speaker_cap
from autune_audio.speakers import UNIDENTIFIED

DIM = 8


def basis(i: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


def tilt(a: np.ndarray, b: np.ndarray, amount: float) -> np.ndarray:
    v = a + amount * b
    return (v / np.linalg.norm(v)).astype(np.float32)


def tracker(**kwargs: object) -> SpeakerTracker:
    defaults: dict[str, object] = {"threshold": 0.6, "min_seconds": 1.0}
    defaults.update(kwargs)
    return SpeakerTracker(**defaults)  # type: ignore[arg-type]


def test_the_first_utterance_opens_speaker_one() -> None:
    t = tracker()
    assert t.label(basis(0), 3.0) == f"{UNIDENTIFIED} 1"
    assert t.clusters == 1


def test_a_similar_voice_joins_and_moves_the_centroid() -> None:
    # v · e1 = 1/√1.49 ≈ 0.82: joins. The centroid becomes normalise(e1 + v)
    # ≈ (0.954, 0.300, …). w = (0.55, 0.835, …) scores 0.55 against e1 alone
    # (below 0.6: would open) but ≈ 0.78 against the moved centroid (joins).
    v = tilt(basis(0), basis(1), 0.7)
    w = np.zeros(DIM, dtype=np.float32)
    w[0], w[1] = 0.55, 0.835

    moved = tracker()
    moved.label(basis(0), 3.0)
    assert moved.label(v, 3.0) == "화자 1"
    assert moved.clusters == 1
    assert moved.label(w, 3.0) == "화자 1"

    unmoved = tracker()
    unmoved.label(basis(0), 3.0)
    assert unmoved.label(w, 3.0) == "화자 2"


def test_a_different_voice_opens_speaker_two() -> None:
    t = tracker()
    t.label(basis(0), 3.0)
    assert t.label(basis(1), 3.0) == "화자 2"
    assert t.clusters == 2


def test_labels_never_change_once_given() -> None:
    t = tracker()
    first = t.label(basis(0), 3.0)
    t.label(basis(1), 3.0)
    for _ in range(5):
        t.label(tilt(basis(1), basis(0), 0.3), 3.0)
    assert t.label(basis(0), 3.0) == first == "화자 1"
    assert t.clusters == 2


def test_the_cap_forces_assignment_below_the_threshold() -> None:
    t = tracker(max_speakers=1)
    t.label(basis(0), 3.0)
    assert t.label(basis(1), 3.0) == "화자 1"  # orthogonal, but the room has one person
    assert t.clusters == 1


def test_the_cap_still_updates_the_centroid() -> None:
    # Two clusters, e1 and e2, and the room is capped at two. e3 scores 0
    # against both; the tie goes to the first, whose centroid becomes
    # (e1+e3)/√2. A voice leaning toward e3 -- (e3 + 0.5·e2), scoring 0.63
    # against the moved centroid and 0.45 against e2 -- then goes to 화자 1.
    # Had the forced assignment not moved the centroid it would score 0
    # against e1 and go to 화자 2.
    t = tracker(max_speakers=2)
    t.label(basis(0), 3.0)
    t.label(basis(1), 3.0)
    assert t.label(basis(2), 3.0) == "화자 1"
    assert t.clusters == 2
    assert t.label(tilt(basis(2), basis(1), 0.5), 3.0) == "화자 1"


def test_a_short_utterance_is_assigned_but_opens_nothing() -> None:
    t = tracker()
    t.label(basis(0), 3.0)
    assert t.label(basis(1), 0.4) == "화자 1"
    assert t.clusters == 1


def test_a_short_utterance_does_not_move_the_centroid() -> None:
    t = tracker()
    t.label(basis(0), 3.0)
    t.label(basis(1), 0.4)  # short: assigned to 화자 1, must not drag it
    # A voice halfway between would still be nearer 화자 1's untouched centroid
    # than anything else, and a genuinely new voice still opens 화자 2.
    assert t.label(basis(1), 3.0) == "화자 2"


def test_a_short_first_utterance_still_opens_speaker_one() -> None:
    t = tracker()
    assert t.label(basis(0), 0.3) == "화자 1"
    assert t.clusters == 1


def test_an_unnormalised_vector_is_normalised() -> None:
    t = tracker()
    t.label(basis(0) * 7.0, 3.0)
    assert t.label(basis(0) * 0.01, 3.0) == "화자 1"


def test_the_threshold_decides_the_boundary() -> None:
    v = tilt(basis(0), basis(1), 1.0)  # cosine with e1 = 1/√2 ≈ 0.707
    loose = tracker(threshold=0.7)
    loose.label(basis(0), 3.0)
    assert loose.label(v, 3.0) == "화자 1"
    strict = tracker(threshold=0.71)
    strict.label(basis(0), 3.0)
    assert strict.label(v, 3.0) == "화자 2"


def test_the_cap_comes_from_the_exact_count_first() -> None:
    assert speaker_cap(AudioSettings(diarization_num_speakers=2, diarization_max_speakers=5)) == 2
    assert speaker_cap(AudioSettings(diarization_max_speakers=5)) == 5
    assert speaker_cap(AudioSettings()) is None
