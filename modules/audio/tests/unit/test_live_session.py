"""Frames in, masked rows out; pause drops; stop flushes.

The transcriber is faked with a function of the segment's length so the tests
say something about the session and nothing about Whisper.
"""

from __future__ import annotations

import traceback

import numpy as np
import pytest

from autune_audio.live.segmenter import Segmenter
from autune_audio.live.session import NO_SPEAKER, LiveSession, TranscribeFailed
from autune_audio.live.speakers import SpeakerTracker
from autune_audio.live.transcriber import Transcriber
from autune_audio.schemas import (
    SAMPLE_RATE,
    Transcription,
    Waveform,
    Word,
)
from autune_audio.schemas import (
    Segment as WhisperSegment,
)

FRAME = SAMPLE_RATE // 5  # 200 ms


def pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def tone(ms: int) -> np.ndarray:
    t = np.arange(SAMPLE_RATE * ms // 1000, dtype=np.float32) / SAMPLE_RATE
    return (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(ms: int) -> np.ndarray:
    return np.zeros(SAMPLE_RATE * ms // 1000, dtype=np.float32)


def energy(frame: np.ndarray) -> float:
    return float(min(1.0, np.sqrt(np.mean(frame * frame)) * 4))


def saying(text: str, *, probability: float = 0.9):
    """A fake transcriber that says ``text`` for any audio."""

    def transcribe(waveform: Waveform) -> Transcription:
        words = tuple(
            Word(start=0.0, end=waveform.duration, text=part, probability=probability)
            for part in text.split()
        )
        segment = WhisperSegment(start=0.0, end=waveform.duration, text=text, words=words)
        return Transcription(
            segments=(segment,),
            language="ko",
            language_probability=1.0,
            duration=waveform.duration,
        )

    return Transcriber(transcribe=transcribe, warm_up=lambda: None)


def session(transcriber: Transcriber) -> LiveSession:
    return LiveSession(
        segmenter=Segmenter(speech_probability=energy, min_silence_ms=700), transcriber=transcriber
    )


async def feed(live: LiveSession, audio: np.ndarray) -> list:
    rows = []
    for i in range(0, len(audio) - len(audio) % FRAME, FRAME):
        rows.extend(await live.on_frame(pcm(audio[i : i + FRAME])))
    return rows


@pytest.mark.asyncio
async def test_an_utterance_becomes_one_masked_row() -> None:
    live = session(saying("연락처는 010-1234-5678입니다"))

    rows = await feed(live, np.concatenate([tone(1000), silence(1000)]))

    [row] = rows
    assert row.text == "연락처는 010-****-5678입니다"
    assert row.id.startswith("utt_live_")
    assert row.speaker_id is None
    assert row.start == 0.0 and 0.9 <= row.end <= 1.3
    assert 0 <= row.confidence <= 1


@pytest.mark.asyncio
async def test_a_guess_below_the_confidence_floor_is_not_a_row() -> None:
    """The first microphone runs put "Logic 감사합니다" on screen at 0.23; the
    words the person said scored 0.5 and up. A row is display, and a guess
    is not worth showing -- the stored path remakes it."""
    live = LiveSession(
        segmenter=Segmenter(speech_probability=energy, min_silence_ms=700),
        transcriber=saying("Logic 감사합니다", probability=0.2),
        min_confidence=0.35,
    )

    rows = await feed(live, np.concatenate([tone(1000), silence(1000)]))

    assert rows == []
    assert live.rows_sent == 0


@pytest.mark.asyncio
async def test_an_empty_transcription_is_not_a_row() -> None:
    """The VAD can take a breath for speech; the model then hears nothing.
    That is no row, and it is not counted as one."""

    def nothing(waveform: Waveform) -> Transcription:
        return Transcription(
            segments=(WhisperSegment(start=0.0, end=waveform.duration, text="  ", words=()),),
            language="ko",
            language_probability=1.0,
            duration=waveform.duration,
        )

    live = session(Transcriber(transcribe=nothing, warm_up=lambda: None))

    rows = await feed(live, np.concatenate([tone(1000), silence(1000)]))
    assert await feed(live, tone(1000)) == []  # an open utterance for stop()
    flushed = await live.stop()

    assert rows == []
    assert flushed == []
    assert live.rows_sent == 0


@pytest.mark.asyncio
async def test_paused_frames_are_dropped() -> None:
    live = session(saying("무시"))
    live.pause()

    rows = await feed(live, np.concatenate([tone(1000), silence(1000)]))

    assert rows == []
    assert live.state == "paused"


@pytest.mark.asyncio
async def test_stop_flushes_the_open_utterance() -> None:
    live = session(saying("마지막"))
    assert await feed(live, tone(1000)) == []  # no silence yet

    rows = await live.stop()

    assert [row.text for row in rows] == ["마지막"]
    assert live.state == "ended"


@pytest.mark.asyncio
async def test_stop_twice_is_idempotent() -> None:
    live = session(saying("마지막"))
    assert await feed(live, tone(1000)) == []  # no silence yet
    await live.stop()

    rows = await live.stop()

    assert rows == []
    assert live.state == "ended"


@pytest.mark.asyncio
async def test_an_odd_length_frame_is_truncated_not_raised_on() -> None:
    live = session(saying("무관"))

    rows = await live.on_frame(pcm(silence(200)) + b"\x00")

    assert rows == []


@pytest.mark.asyncio
async def test_a_transcribe_failure_is_the_routes_to_report_and_the_session_continues() -> None:
    calls = 0

    def flaky(waveform: Waveform) -> Transcription:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("model fell over")
        return saying("두 번째")._transcribe(waveform)  # type: ignore[attr-defined]

    live = session(Transcriber(transcribe=flaky, warm_up=lambda: None))
    utterance = np.concatenate([tone(1000), silence(1000)])

    with pytest.raises(TranscribeFailed):
        await feed(live, utterance)
    rows = await feed(live, utterance)

    assert [row.text for row in rows] == ["두 번째"]
    assert live.state == "recording"


@pytest.mark.asyncio
async def test_transcribe_failed_does_not_carry_the_causes_message() -> None:
    """The cause is quotable (an ffmpeg-style error can echo what it read);
    TranscribeFailed must not let it print through the traceback chain."""

    def blows_up(waveform: Waveform) -> Transcription:
        raise RuntimeError("quoting: 900101-1234567")

    live = session(Transcriber(transcribe=blows_up, warm_up=lambda: None))

    with pytest.raises(TranscribeFailed) as excinfo:
        await feed(live, np.concatenate([tone(1000), silence(1000)]))

    caught = excinfo.value
    assert "1234567" not in "".join(traceback.format_exception(caught))
    assert caught.kind == "RuntimeError"


@pytest.mark.asyncio
async def test_the_unmasked_text_does_not_reach_the_log(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # ``autune_core.get_logger`` is structlog with ``PrintLoggerFactory``,
    # which writes straight to stdout -- not through stdlib ``logging``, so
    # ``caplog`` never sees it. ``capsys`` reads the same stream the process
    # actually writes to.
    live = session(saying("주민번호 900101-1234567입니다"))

    await feed(live, np.concatenate([tone(1000), silence(1000)]))

    out = capsys.readouterr().out
    assert "900101-1234567" not in out
    assert "1234567" not in out


@pytest.mark.asyncio
async def test_warm_up_delegates_to_the_transcriber() -> None:
    calls = 0

    class FakeTranscriber:
        async def warm_up(self) -> None:
            nonlocal calls
            calls += 1

    live = LiveSession(
        segmenter=Segmenter(speech_probability=energy, min_silence_ms=700),
        transcriber=FakeTranscriber(),
    )

    await live.warm_up()

    assert calls == 1


class FakeEmbedder:
    """Returns the vector the caller queued for each call; records what it saw."""

    def __init__(self, *vectors: np.ndarray, fail_after: int | None = None) -> None:
        self.queue = list(vectors)
        self.seen: list[object] = []
        self.warmed = 0
        self.fail_after = fail_after

    def warm_up(self) -> None:
        self.warmed += 1

    def embed(self, waveform: Waveform) -> np.ndarray:
        self.seen.append(waveform)
        if self.fail_after is not None and len(self.seen) > self.fail_after:
            raise RuntimeError("secret /tmp/path in the message")
        return self.queue.pop(0)


def basis(i: int, dim: int = 4) -> np.ndarray:
    v = np.zeros(dim, dtype=np.float32)
    v[i] = 1.0
    return v


def two_utterances() -> np.ndarray:
    return np.concatenate([tone(1000), silence(1000), tone(1000), silence(1000)])


def labelled(
    transcriber: Transcriber, embedder: object | None, *, min_confidence: float | None = None
) -> LiveSession:
    return LiveSession(
        segmenter=Segmenter(speech_probability=energy, min_silence_ms=700),
        transcriber=transcriber,
        min_confidence=min_confidence,
        embedder=embedder,  # type: ignore[arg-type]
        tracker=SpeakerTracker(threshold=0.6, min_seconds=1.0),
    )


@pytest.mark.asyncio
async def test_rows_carry_the_tracker_label() -> None:
    embedder = FakeEmbedder(basis(0), basis(1))
    live = labelled(saying("안녕하세요"), embedder)

    rows = await feed(live, two_utterances())

    assert [r.speaker for r in rows] == ["화자 1", "화자 2"]
    assert all(r.speaker_id is None for r in rows)


@pytest.mark.asyncio
async def test_the_embedder_sees_the_audio_and_never_the_text() -> None:
    embedder = FakeEmbedder(basis(0))
    live = labelled(saying("연락처는 010-1234-5678입니다"), embedder)

    [row] = await feed(live, np.concatenate([tone(1000), silence(1000)]))

    [seen] = embedder.seen
    assert isinstance(seen, Waveform)
    assert 0.9 <= seen.duration <= 1.3
    assert row.text == "연락처는 010-****-5678입니다"


@pytest.mark.asyncio
async def test_a_dropped_row_does_not_reach_the_tracker() -> None:
    embedder = FakeEmbedder(basis(0))
    live = labelled(saying("Logic 감사합니다", probability=0.2), embedder, min_confidence=0.35)

    assert await feed(live, np.concatenate([tone(1000), silence(1000)])) == []
    assert embedder.seen == []
    assert live.tracker.clusters == 0


@pytest.mark.asyncio
async def test_no_embedder_means_the_degraded_label() -> None:
    live = labelled(saying("안녕하세요"), None)
    rows = await feed(live, two_utterances())
    assert [r.speaker for r in rows] == [NO_SPEAKER, NO_SPEAKER]
    assert NO_SPEAKER == "?"


@pytest.mark.asyncio
async def test_an_embedding_failure_degrades_the_rest_of_the_session(
    capsys: pytest.CaptureFixture[str],
) -> None:
    embedder = FakeEmbedder(basis(0), fail_after=1)
    live = labelled(saying("안녕하세요"), embedder)

    rows = await feed(live, np.concatenate([two_utterances(), tone(1000), silence(1000)]))

    assert [r.speaker for r in rows] == ["화자 1", "?", "?"]
    assert len(embedder.seen) == 2  # switched off after the failure, not retried
    out = capsys.readouterr().out
    assert out.count("live_speaker_failed") == 1
    assert "RuntimeError" in out
    assert "secret" not in out and "/tmp" not in out


@pytest.mark.asyncio
async def test_warm_up_that_cannot_load_the_embedder_still_leaves_the_channel_up(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Broken(FakeEmbedder):
        def warm_up(self) -> None:
            raise ImportError("pyannote")

    live = labelled(saying("안녕하세요"), Broken())
    await live.warm_up()
    rows = await feed(live, np.concatenate([tone(1000), silence(1000)]))

    assert [r.speaker for r in rows] == ["?"]
    assert "live_speaker_unavailable" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_warm_up_warms_the_embedder_after_the_transcriber() -> None:
    embedder = FakeEmbedder()
    live = labelled(saying("안녕하세요"), embedder)
    await live.warm_up()
    assert embedder.warmed == 1


def test_the_default_tracker_reads_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from autune_audio.config import AudioSettings
    from autune_audio.live import session as session_module

    monkeypatch.setattr(
        session_module,
        "get_settings",
        lambda: AudioSettings(live_speaker_threshold=0.42, live_speaker_min_s=2.0),
    )
    live = session(saying("x"))
    assert live.tracker.threshold == 0.42
    assert live.tracker.max_speakers is None
