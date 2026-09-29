"""Which person a voice is offered as, given vectors and nothing else.

Unit vectors along the axes, so every similarity in here is arithmetic the
reader can check: e0 . e0 = 1, e0 . e1 = 0, and the mean of e0 and e1 is
(1,1)/sqrt(2), which sits at 0.707 from each.
"""

from __future__ import annotations

import numpy as np
import pytest

from autune_audio.identification import Candidate, Profile, best_candidate, mean_vector

DIM = 8


def axis(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


def profile(user_id: str, *vectors: list[float], model_version: str = "m1") -> Profile:
    return Profile(
        user_id=user_id,
        display_name=user_id.upper(),
        vectors=tuple(tuple(v) for v in vectors),
        model_version=model_version,
    )


def test_the_mean_of_one_vector_is_that_vector() -> None:
    assert mean_vector([axis(0)]) == pytest.approx(np.array(axis(0)))


def test_the_mean_is_unit_length_and_between_its_inputs() -> None:
    mean = mean_vector([axis(0), axis(1)])
    assert float(np.linalg.norm(mean)) == pytest.approx(1.0)
    assert float(mean @ np.array(axis(0))) == pytest.approx(0.7071, abs=1e-4)


def test_a_mean_with_no_direction_is_an_error() -> None:
    with pytest.raises(ValueError):
        mean_vector([axis(0), [-x for x in axis(0)]])
    with pytest.raises(ValueError):
        mean_vector([[float("nan")] * DIM])


def test_the_nearest_profile_above_the_threshold_wins() -> None:
    found = best_candidate(
        axis(0),
        [profile("alice", axis(0)), profile("bob", axis(1))],
        model_version="m1",
        threshold=0.7,
    )
    assert found == Candidate(user_id="alice", display_name="ALICE", similarity=pytest.approx(1.0))


def test_the_higher_of_two_qualifying_profiles_wins() -> None:
    """Both clear a 0.7 threshold: alice at 1.0, bob at ~0.7071.

    Ordered bob-first so a "first match wins" implementation would return bob.
    Bob's mean of e0 and e1 is (1,1,0,…)/√2, whose dot with e0 is 0.7071.
    """
    found = best_candidate(
        axis(0),
        [profile("bob", axis(0), axis(1)), profile("alice", axis(0))],
        model_version="m1",
        threshold=0.7,
    )
    assert found is not None
    assert found.user_id == "alice"
    assert found.similarity == pytest.approx(1.0)


def test_nothing_below_the_threshold_is_offered() -> None:
    # e0 against the mean of e0 and e1 is 0.707, just under 0.71.
    assert (
        best_candidate(
            axis(0), [profile("alice", axis(0), axis(1))], model_version="m1", threshold=0.71
        )
        is None
    )
    assert (
        best_candidate(
            axis(0), [profile("alice", axis(0), axis(1))], model_version="m1", threshold=0.70
        )
        is not None
    )


def test_a_profile_from_another_model_is_not_a_candidate() -> None:
    assert (
        best_candidate(
            axis(0),
            [profile("alice", axis(0), model_version="m2")],
            model_version="m1",
            threshold=0.7,
        )
        is None
    )


def test_no_profiles_is_no_candidate() -> None:
    assert best_candidate(axis(0), [], model_version="m1", threshold=0.7) is None


def test_a_profile_whose_vectors_cancel_is_skipped_not_fatal() -> None:
    """One bad profile must not stop a good one from being offered."""
    broken = profile("bob", axis(1), [-x for x in axis(1)])
    found = best_candidate(
        axis(0), [broken, profile("alice", axis(0))], model_version="m1", threshold=0.7
    )
    assert found is not None
    assert found.user_id == "alice"
