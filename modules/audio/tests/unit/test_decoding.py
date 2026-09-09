"""Decoding turns every accepted upload into the same waveform shape."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from autune_audio.decoding import DecodeError, decode, ffmpeg_available
from autune_audio.schemas import SAMPLE_RATE

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg is not installed")


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """Three seconds of tone at 44.1 kHz — deliberately not the target rate."""
    path = tmp_path / "source.wav"
    t = np.linspace(0, 3, 44100 * 3, endpoint=False)
    sf.write(path, (np.sin(2 * np.pi * 440 * t) * 0.3).astype(np.float32), 44100)
    return path


def _convert(source: Path, suffix: str, codec: str | None) -> Path:
    # A distinct name: for wav the suffix matches the source, and ffmpeg would
    # otherwise be reading the file it is overwriting.
    out = source.with_name(f"converted{suffix}")
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    if codec:
        command += ["-c:a", codec]
    subprocess.run(command + [str(out), "-y"], check=True)
    return out


@pytest.mark.parametrize(
    ("suffix", "codec"),
    [(".wav", None), (".mp3", "libmp3lame"), (".m4a", "aac")],
    ids=["wav", "mp3", "m4a"],
)
def test_every_accepted_format_decodes_to_the_same_shape(
    source: Path, suffix: str, codec: str | None
) -> None:
    """m4a is why this goes through ffmpeg: libsndfile does not read it."""
    waveform = decode(_convert(source, suffix, codec))
    assert waveform.sample_rate == SAMPLE_RATE
    assert waveform.samples.dtype == np.float32
    assert waveform.samples.ndim == 1, "must be mono"
    assert 2.9 < waveform.duration < 3.1


def test_stereo_becomes_mono(tmp_path: Path) -> None:
    path = tmp_path / "stereo.wav"
    t = np.linspace(0, 1, 16000, endpoint=False)
    stereo = np.stack([np.sin(2 * np.pi * 300 * t), np.sin(2 * np.pi * 600 * t)], axis=1)
    sf.write(path, (stereo * 0.3).astype(np.float32), 16000)
    assert decode(path).samples.ndim == 1


def test_a_missing_file_says_so(tmp_path: Path) -> None:
    with pytest.raises(DecodeError, match="no such recording"):
        decode(tmp_path / "absent.wav")


def test_a_file_that_is_not_audio_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("이것은 오디오가 아닙니다")
    with pytest.raises(DecodeError):
        decode(path)


def test_the_error_never_quotes_the_recording(tmp_path: Path) -> None:
    """ffmpeg's stderr can echo stream metadata; only the exit code is reported.

    An exception message reaches error tracking, which is a third party.
    """
    path = tmp_path / "secret-project-kickoff.txt"
    path.write_text("010-1234-5678")
    with pytest.raises(DecodeError) as caught:
        decode(path)
    assert "010-1234-5678" not in str(caught.value)


def test_the_waveform_repr_does_not_carry_the_audio(source: Path) -> None:
    """A waveform is raw audio; its repr may reach a log line."""
    waveform = decode(_convert(source, ".wav", None))
    assert "array" not in repr(waveform)
    assert "3.0s" in repr(waveform)
