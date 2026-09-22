"""Utterance boundaries, cut by silence.

The VAD is injected: these tests use signal energy so they run without a model
and cannot flake on one. The real one (silero, inside faster-whisper) is wired
in ``session.py`` and covered there.
"""

from __future__ import annotations

import numpy as np
import pytest

from autune_audio.live.segmenter import Segmenter
from autune_audio.schemas import SAMPLE_RATE

FRAME_MS = 200
FRAME = SAMPLE_RATE * FRAME_MS // 1000


def tone(ms: int) -> np.ndarray:
    t = np.arange(SAMPLE_RATE * ms // 1000, dtype=np.float32) / SAMPLE_RATE
    return (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(ms: int) -> np.ndarray:
    return np.zeros(SAMPLE_RATE * ms // 1000, dtype=np.float32)


def energy(frame: np.ndarray) -> float:
    return float(min(1.0, np.sqrt(np.mean(frame * frame)) * 4))


def frames(audio: np.ndarray):
    for i in range(0, len(audio) - len(audio) % FRAME, FRAME):
        yield audio[i : i + FRAME]


def run(audio: np.ndarray, **kw) -> list:
    # The fixtures leave 800 ms gaps; the tests are about the rules, not the
    # production silence, which the route sets from live_min_silence_ms.
    kw.setdefault("min_silence_ms", 700)
    segmenter = Segmenter(speech_probability=energy, **kw)
    out = []
    for frame in frames(audio):
        out.extend(segmenter.feed(frame))
    last = segmenter.flush()
    if last is not None:
        out.append(last)
    return out


def test_two_utterances_with_silence_between_are_two_segments() -> None:
    audio = np.concatenate([tone(1000), silence(800), tone(1000), silence(800)])

    segments = run(audio)

    assert len(segments) == 2
    assert segments[0].start == 0.0
    assert 0.9 <= segments[0].end - segments[0].start <= 1.3
    assert 1.6 <= segments[1].start <= 2.0


def test_a_blip_shorter_than_min_speech_is_not_a_segment() -> None:
    audio = np.concatenate([silence(400), tone(200), silence(1000)])

    assert run(audio) == []


def test_continuous_speech_is_cut_at_the_maximum() -> None:
    audio = tone(31_000)

    segments = run(audio, max_segment_s=30.0)

    assert len(segments) == 2
    assert 29.8 <= segments[0].end - segments[0].start <= 30.2
    assert segments[1].start >= 29.8


def test_silence_alone_produces_nothing_and_holds_nothing() -> None:
    segmenter = Segmenter(speech_probability=energy, min_silence_ms=700)
    for frame in frames(silence(5000)):
        assert segmenter.feed(frame) == []
    assert segmenter.flush() is None


def test_timestamps_come_from_the_frame_count() -> None:
    """Never the wall clock: a slow transcribe must not move the next row."""
    audio = np.concatenate([silence(1000), tone(600), silence(800)])

    [segment] = run(audio)

    assert abs(segment.start - 1.0) <= FRAME_MS / 1000
    assert abs(segment.end - 1.6) <= 2 * FRAME_MS / 1000


def test_flush_returns_the_open_segment_on_stop() -> None:
    segmenter = Segmenter(speech_probability=energy, min_silence_ms=700)
    for frame in frames(tone(1000)):
        assert segmenter.feed(frame) == []  # no silence yet, nothing emitted

    last = segmenter.flush()

    assert last is not None
    assert 0.8 <= last.end - last.start <= 1.2
    assert segmenter.flush() is None


def test_the_default_vad_sees_the_frame_with_its_recent_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """silero is scored on a window that ends with the frame, never on the
    200 ms frame alone: a soft syllable in the middle of a sentence read as
    silence when the model had no memory of the voice around it."""
    from autune_audio.live import segmenter as module

    seen: list[tuple[int, int]] = []

    def fake(audio: np.ndarray, tail: int) -> float:
        seen.append((len(audio), tail))
        return 1.0

    monkeypatch.setattr(module, "_silero_probability", fake)
    segmenter = Segmenter()
    for frame in frames(tone(2000)):
        segmenter.feed(frame)

    assert seen[0] == (FRAME, FRAME)  # the first frame has no past
    assert all(tail == FRAME for _, tail in seen)
    longest = max(length for length, _ in seen)
    assert FRAME < longest <= int(module.VAD_CONTEXT_S * SAMPLE_RATE) + FRAME
