"""Module A settings. Environment prefix ``AUTUNE_AUDIO_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
"""The largest recording an upload endpoint accepts. Matches the dropzone on
design screen S03.

Enforced while the bytes are written rather than from ``UploadFile.size``: that
is a number the client sent, and it is ``None`` on a request with no
Content-Length.

Here rather than beside either endpoint because both of them enforce it — the
real upload route and the local dev page — and a limit that is written twice is
a limit that ends up meaning two things.
"""


class AudioSettings(BaseSettings):
    # env_file mirrors autune_core.Settings: without it a module reads only
    # real environment variables and silently ignores .env.
    model_config = SettingsConfigDict(env_prefix="AUTUNE_AUDIO_", env_file=".env", extra="ignore")

    whisper_model: str = "large-v3"
    device: str = "cpu"
    """``cpu`` or ``cuda``. Whisper falls back to CPU inference when there is no GPU."""

    temp_dir: str = "/tmp/autune-audio"
    """Where a recording lives while a task runs, and only then.

    Every path that writes a recording goes through
    ``storage.recording_on_disk``, which deletes it in a ``finally`` and confirms
    it is gone. That primitive also refuses a directory the file could survive in
    — a cloud-sync folder, or anywhere inside the checkout — so "never point this
    at a synced folder" is now enforced rather than requested.

    See docs/architecture/privacy.md section 1.
    """

    orphan_after_hours: int = 6
    """How long a queued or running job may hold a recording before the sweep
    treats it as abandoned, fails it, and deletes the file.

    Six hours is three times the longest meeting the pipeline is sized for at
    the measured ~1.27x real time, plus a queue wait. A job older than that has
    no worker; its file is a recording with no owner (privacy.md section 1).
    See ``service.sweep_orphans``.
    """

    hf_token: str = ""
    """Hugging Face token for the gated pyannote models.

    The licence must be accepted on **three** repositories, not one:
    ``speaker-diarization-3.1``, ``segmentation-3.0``, and
    ``speaker-diarization-community-1``. The pipeline loads the latter two
    itself, so a missing acceptance surfaces partway through loading with an
    error naming a model you never asked for.

    Empty is allowed so the API can boot without it; diarization then fails with
    a usable message instead. See docs/engineering/environments.md.
    """

    beam_size: int = 5
    """Whisper beam width. Higher is slower and marginally better; 5 is the
    faster-whisper default and what the processing-time target assumes."""

    model_cache: str = ""
    """Where model weights are downloaded. Empty uses the library default.
    Weights are never committed — see docs/engineering/environments.md."""

    glossary_mode: str = "hotwords"
    """How the meeting glossary reaches Whisper: ``hotwords``, ``prompt``, ``both``.

    Not interchangeable, and the difference only shows at meeting length.
    Measured on the 11m37s evaluation recording (issue #118): term accuracy 31%
    without a glossary, 86% with ``hotwords``, 28% with ``prompt``, 52% with
    both. A setting rather than a constant so the comparison can be re-run on a
    new model without editing code. See ``pipeline._glossary_kwargs``.
    """

    recogniser: Literal["spoken_numbers", "none"] = "spoken_numbers"
    """The second PII detector, behind ``masking.EntityRecogniser``.

    ``spoken_numbers`` finds the five categories in numbers a person read out
    one digit at a time -- the shapes a pattern cannot describe. ``none``
    switches it off, which is how the evaluation harness measures the patterns
    alone and how a leak is attributed to one detector or the other.

    There is no hosted value and there will not be one. This runs over the
    unmasked transcript, and invariant 11 says that string does not leave the
    process it was made in.
    """

    diarization_model: str = "pyannote/speaker-diarization-3.1"
    """Pinned explicitly. Never load a floating "latest"."""

    diarization_num_speakers: int | None = None
    """Exactly how many people spoke, when the room knows. On a muffled
    microphone pyannote split one voice into four clusters (#325); with this
    set it cannot. A deployment-wide knob for now -- one demo, one room --
    and the wrong number for a meeting is worse than none, so it stays unset
    by default. The per-meeting field belongs with S10's attendee list."""

    diarization_min_speakers: int | None = None
    """Lower bound on speakers when the exact count is unknown. Ignored when
    ``diarization_num_speakers`` is set."""

    diarization_max_speakers: int | None = None
    """Upper bound on speakers when the exact count is unknown. Ignored when
    ``diarization_num_speakers`` is set."""

    live_hello_timeout_s: float = 5.0
    """How long a live connection may sit without sending ``hello``."""

    live_max_session_s: float = 3 * 60 * 60
    """The longest live session, matching the 3-hour ceiling on S03. Past it
    the session ends normally; the recording is in the browser."""

    live_max_frame_bytes: int = 32 * 1024
    """One second of PCM16 at 16 kHz. A bigger frame is dropped, not buffered."""

    live_frame_ms: int = 200
    """What the browser is asked to send. Informational; the server accepts
    any frame under ``live_max_frame_bytes``."""

    live_whisper_model: str = "large-v3-turbo"
    """The live channel's model. Turbo keeps large-v3's encoder and cuts the
    decoder to four layers: on CPU a row costs about half of large-v3 for
    Korean that reads the same. The stored path keeps ``whisper_model``."""

    live_cpu_threads: int = 0
    """CTranslate2 threads for the live model. 0 leaves the choice to
    CTranslate2 (four on the laptop that measured this). One transcription
    runs at a time on the live path, so the count can be the machine's
    performance cores -- 10 on that laptop, which halved the decode time --
    without contending with anything but itself."""

    live_transcriber_impl: Literal["auto", "faster_whisper", "mlx"] = "auto"
    """Which engine transcribes a live utterance (``live/backends.py``).

    ``faster_whisper`` is the stored path's engine on the live model and
    follows ``device`` -- CUDA where there is an NVIDIA GPU. ``mlx`` is
    mlx-whisper on Apple silicon's GPU, the only way to a GPU on a Mac; it
    needs the ``mlx`` extra. ``auto`` picks ``mlx`` where that is installed
    and can run, ``faster_whisper`` everywhere else."""

    live_mlx_model: str = "mlx-community/whisper-large-v3-turbo"
    """The mlx-whisper weights, a Hugging Face repo. The MLX conversion of
    the same turbo model the CTranslate2 path uses."""

    live_min_silence_ms: int = 1000
    """How much silence ends a live utterance. 700 ms cut real speech at
    every mid-sentence breath; on the microphone captures 1000 ms kept
    sentences whole and let a breath's noise join the sentence before it
    instead of becoming a row of its own. Every 100 ms here is 100 ms more
    lag on every row; 1300 merges sentences a person would keep apart."""

    live_min_confidence: float = 0.35
    """A live row below this mean word probability is not sent. On the first
    real-microphone runs the hallucinated fragments scored 0.08-0.25 and
    real speech 0.5-0.95; the stored path remakes every row, so a dropped
    one costs nothing but a moment on screen."""

    live_beam_size: int = 5
    """Beam width on the live path. Width 5 costs turbo about 0.3 s more per
    utterance than width 1 and is what the stored path uses, so a live row
    and the row that replaces it after the upload read the same."""

    @model_validator(mode="after")
    def _warn_on_cuda_without_token(self) -> AudioSettings:
        """A GPU with no token is a configuration someone meant to finish."""
        if self.device == "cuda" and not self.hf_token:
            raise ValueError(
                "AUTUNE_AUDIO_DEVICE=cuda but AUTUNE_AUDIO_HF_TOKEN is empty; "
                "diarization would fail after the recording was already uploaded"
            )
        return self

    def require_hf_token(self) -> str:
        """The token, or an error naming every repository that needs accepting."""
        if not self.hf_token:
            raise ValueError(
                "AUTUNE_AUDIO_HF_TOKEN is not set. Create a Hugging Face token with "
                "read access to gated repos, then accept the licence on all three: "
                "pyannote/speaker-diarization-3.1, pyannote/segmentation-3.0, and "
                "pyannote/speaker-diarization-community-1. The pipeline loads the "
                "last two itself, so missing either fails partway through loading."
            )
        return self.hf_token


@lru_cache
def get_settings() -> AudioSettings:
    return AudioSettings()
