"""Module A settings. Environment prefix ``AUTUNE_AUDIO_``.

Document every new variable in docs/engineering/environments.md and add it to
.env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    upload_sweep_hours: int = 24
    """How long a staged upload may sit before ``storage.sweep_stale_uploads``
    treats it as orphaned and deletes it.

    Has to exceed **the wait in the queue plus the run**, not just the run: the
    mtime the sweep reads is when the upload was staged, which is before the
    task was picked up. A backlog is what makes those two differ, and a backlog
    is also when the sweep is most likely to meet a file that is still wanted.
    The sweep cannot tell a recording still being transcribed from one nobody
    came back for — it only sees mtime — so the threshold is the whole defence.

    24 hours is far above the worst case measured so far (about 1.27x real time)
    and still bounds how long a recording can outlive its request.
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

    diarization_model: str = "pyannote/speaker-diarization-3.1"
    """Pinned explicitly. Never load a floating "latest"."""

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
