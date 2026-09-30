"""Which person a voice is offered as.

Vectors in, a candidate or nothing out. No database, no model, no request --
the part with the reasoning in it, so it can be read and tested on its own,
the way ``speakers`` is to diarization.

**A candidate is a suggestion.** Nothing here writes anybody's name onto an
utterance; ``service.assign_speaker`` does that, and only because a person
pressed a button. A similarity high enough to show is not high enough to
record: the cost of being wrong is a commitment filed under somebody who
never made it (``UnidentifiedSpeaker.tsx``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from autune_core import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Profile:
    """One person's confirmed voice, as every vector they have confirmed."""

    user_id: str
    display_name: str
    vectors: tuple[tuple[float, ...], ...]
    model_version: str


@dataclass(frozen=True)
class Candidate:
    user_id: str
    display_name: str
    similarity: float


def mean_vector(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    """The unit-length mean direction of ``vectors``.

    Raises when there is no direction to return -- an empty input, a
    non-finite value, or vectors that cancel out. The caller decides what that
    means; ``best_candidate`` skips such a profile rather than failing the
    request.
    """
    # Vectors arrive from the embedder as float32; cast here to avoid silent
    # upcast to float64 and to preserve the precision they were computed in.
    stacked = np.asarray(vectors, dtype=np.float32)
    if stacked.size == 0:
        raise ValueError("no vectors")
    total = stacked.sum(axis=0)
    norm = float(np.linalg.norm(total))
    if not np.isfinite(norm) or norm == 0.0:
        raise ValueError("the vectors have no mean direction")
    return total / norm


def best_candidate(
    observation: Sequence[float],
    profiles: Sequence[Profile],
    *,
    model_version: str,
    threshold: float,
) -> Candidate | None:
    """The nearest profile to ``observation``, or ``None``.

    Only profiles from the same ``model_version`` are considered: the same
    voice sits somewhere else in another checkpoint's space, so a comparison
    across the two is a number with no meaning.

    Equal similarities are broken by the order supplied: strict ``>``
    comparison keeps the first-matching profile, so the caller owns the tie.
    """
    try:
        vector = mean_vector([observation])
    except ValueError:
        return None

    best: Candidate | None = None
    for candidate in profiles:
        if candidate.model_version != model_version:
            continue
        try:
            profile_mean = mean_vector(candidate.vectors)
        except ValueError:
            # One unusable profile does not cost the others their chance.
            log.info("identification_profile_unusable", user_id=candidate.user_id)
            continue
        similarity = float(profile_mean @ vector)
        if similarity >= threshold and (best is None or similarity > best.similarity):
            best = Candidate(
                user_id=candidate.user_id,
                display_name=candidate.display_name,
                similarity=similarity,
            )
    return best
