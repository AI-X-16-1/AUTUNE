"""Which seconds of a recording stand in for one speaker.

Numbers, not audio: the samples are an index ramp, so a returned waveform says
exactly which part of the original it came from.
"""

from __future__ import annotations

import numpy as np

from autune_audio.schemas import SAMPLE_RATE, Turn, Waveform
from autune_audio.speaker_audio import representative_waveform


def ramp(seconds: float) -> Waveform:
    """Sample i has value i, so a slice identifies itself."""
    return Waveform(samples=np.arange(int(seconds * SAMPLE_RATE), dtype=np.float32))


def seconds_of(waveform: Waveform | None) -> float:
    return 0.0 if waveform is None else round(waveform.duration, 3)


def test_a_speaker_with_too_little_speech_has_no_representative() -> None:
    turns = (Turn(0.0, 1.0, "A"), Turn(5.0, 6.5, "A"))  # 2.5 s in total
    assert representative_waveform(ramp(10), turns, "A", max_seconds=10.0, min_seconds=3.0) is None


def test_the_longest_turns_are_taken_first_and_stay_in_time_order() -> None:
    turns = (
        Turn(0.0, 1.0, "A"),  # 1 s  -- not needed
        Turn(2.0, 6.0, "A"),  # 4 s  -- taken second by position, first by length
        Turn(8.0, 11.0, "A"),  # 3 s  -- taken
        Turn(11.0, 12.0, "B"),
    )
    result = representative_waveform(ramp(13), turns, "A", max_seconds=7.0, min_seconds=3.0)
    assert seconds_of(result) == 7.0
    assert result is not None
    # Time order, not length order: the 2.0-6.0 turn's samples come first.
    assert result.samples[0] == 2.0 * SAMPLE_RATE
    assert result.samples[int(4.0 * SAMPLE_RATE)] == 8.0 * SAMPLE_RATE


def test_it_stops_at_max_seconds() -> None:
    turns = tuple(Turn(float(i), float(i) + 1.0, "A") for i in range(20))
    result = representative_waveform(ramp(25), turns, "A", max_seconds=10.0, min_seconds=3.0)
    assert seconds_of(result) == 10.0


def test_another_speaker_is_not_included() -> None:
    turns = (Turn(0.0, 4.0, "A"), Turn(4.0, 8.0, "B"))
    result = representative_waveform(ramp(9), turns, "A", max_seconds=10.0, min_seconds=3.0)
    assert seconds_of(result) == 4.0
    assert result is not None
    assert float(result.samples.max()) < 4.0 * SAMPLE_RATE


def test_a_label_with_no_turns_has_no_representative() -> None:
    turns = (Turn(0.0, 5.0, "A"),)
    assert representative_waveform(ramp(6), turns, "B", max_seconds=10.0, min_seconds=3.0) is None


def test_a_turn_past_the_end_of_the_audio_is_clipped() -> None:
    """A diarizer may round a turn past the last sample; slicing must not
    invent audio or raise."""
    turns = (Turn(0.0, 4.0, "A"), Turn(4.0, 9.0, "A"))
    result = representative_waveform(ramp(5), turns, "A", max_seconds=10.0, min_seconds=3.0)
    assert seconds_of(result) == 5.0


def test_the_last_turn_is_cut_short_when_the_budget_runs_out() -> None:
    # (0,4) is taken whole (4 s, budget 5 -> 1 s left); (10,13) is the longer
    # candidate next but only its first second fits.
    turns = (Turn(0.0, 4.0, "A"), Turn(10.0, 13.0, "A"))
    result = representative_waveform(ramp(14), turns, "A", max_seconds=5.0, min_seconds=3.0)
    assert seconds_of(result) == 5.0
    assert result is not None
    # The cut piece comes from the *start* of its turn, not the end.
    assert result.samples[int(4.0 * SAMPLE_RATE)] == 10.0 * SAMPLE_RATE
