"""Turn an uploaded file into a waveform Whisper and pyannote can both read.

Uploads arrive as mp3, wav or m4a. libsndfile handles the first two and not the
third, so decoding goes through ffmpeg, which handles all of them and resamples
in the same pass.

**ffmpeg writes to a pipe, not to a file.** A decoded copy on disk would be a
second piece of raw audio to find and delete, and the whole point is to have
fewer. See docs/architecture/privacy.md section 1.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from autune_core import get_logger
from autune_core.errors import AutuneError

from .schemas import SAMPLE_RATE, Waveform

log = get_logger(__name__)

DECODE_TIMEOUT_SECONDS = 600
"""A three-hour upload decodes in well under this. A run that exceeds it is
stuck, not slow."""


class DecodeError(AutuneError):
    code = "audio_decode_failed"
    status_code = 422


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def decode(path: Path) -> Waveform:
    """Decode ``path`` to 16 kHz mono float32, in memory.

    Raises :class:`DecodeError` when the file is not audio we can read. The
    message names the file's own name and never its contents.
    """
    if not path.exists():
        raise DecodeError(f"no such recording: {path.name}")
    if not ffmpeg_available():
        raise DecodeError(
            "ffmpeg is not installed, so uploads cannot be decoded. "
            "brew install ffmpeg (macOS) or apt install ffmpeg (Linux). "
            "See docs/engineering/environments.md"
        )

    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        "-f",
        "wav",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=DECODE_TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise DecodeError(f"decoding {path.name} timed out") from exc

    if result.returncode != 0:
        # ffmpeg's stderr can quote stream metadata, which may carry a filename
        # or title from the recording. Only the return code is reported.
        raise DecodeError(
            f"{path.name} could not be decoded as audio (ffmpeg exit {result.returncode})"
        )

    samples, sample_rate = sf.read(io.BytesIO(result.stdout), dtype="float32", always_2d=False)
    if sample_rate != SAMPLE_RATE:
        raise DecodeError(f"expected {SAMPLE_RATE} Hz from ffmpeg, got {sample_rate}")
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if samples.size == 0:
        raise DecodeError(f"{path.name} contains no audio")

    waveform = Waveform(samples=np.ascontiguousarray(samples, dtype=np.float32))
    log.info("audio_decoded", seconds=round(waveform.duration, 1), source_format=path.suffix)
    return waveform
