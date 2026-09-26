"""Which seconds of a recording stand in for one speaker.

An embedding wants a few uninterrupted seconds of one voice. A diarizer gives
turns, and a meeting's turns for one person are mostly short: "네", "맞아요",
and a handful of real sentences. Taking the longest turns first gets the
sentences; taking them in time order keeps the audio sounding like speech
rather than a cut-up.

Nothing here loads a model. It slices a waveform, which is why it can be
tested with an index ramp and no audio at all.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .schemas import Turn, Waveform


def representative_waveform(
    waveform: Waveform,
    turns: Sequence[Turn],
    label: str,
    *,
    max_seconds: float,
    min_seconds: float,
) -> Waveform | None:
    """``label``'s longest turns, concatenated in time order, up to
    ``max_seconds``.

    ``None`` when the speaker has less than ``min_seconds`` in total: a vector
    from a scrap of speech is noise, and this one could be offered as a
    candidate and confirmed into somebody's profile.
    """
    rate = waveform.sample_rate
    total = len(waveform.samples)
    mine = [
        (turn.start, min(turn.end, total / rate))
        for turn in turns
        if turn.speaker == label and turn.start * rate < total
    ]
    mine = [(start, end) for start, end in mine if end > start]
    if sum(end - start for start, end in mine) < min_seconds:
        return None

    taken: list[tuple[float, float]] = []
    budget = max_seconds
    for start, end in sorted(mine, key=lambda span: span[1] - span[0], reverse=True):
        if budget <= 0:
            break
        end = min(end, start + budget)
        taken.append((start, end))
        budget -= end - start

    if not taken:
        # Non-positive budget asks for no audio.
        return None

    pieces = [waveform.samples[int(start * rate) : int(end * rate)] for start, end in sorted(taken)]
    return Waveform(samples=np.concatenate(pieces), sample_rate=rate)
