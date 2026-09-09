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

    Deleted in a ``finally`` block before the task returns. Never point this at a
    synced folder. See docs/architecture/privacy.md section 1.
    """

    hf_token: str = ""
    """Hugging Face token for the gated pyannote models.

    The licence must be accepted on **two** repositories — the diarization
    pipeline and the segmentation model it loads itself. Empty is allowed so the
    API can boot without it; diarization fails with a usable message instead.
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
        """The token, or an error naming both repositories that need accepting."""
        if not self.hf_token:
            raise ValueError(
                "AUTUNE_AUDIO_HF_TOKEN is not set. Create a Hugging Face token with "
                "read access to gated repos, and accept the licence on BOTH "
                "pyannote/speaker-diarization-3.1 and pyannote/segmentation-3.0 — "
                "the pipeline loads the second one itself."
            )
        return self.hf_token


@lru_cache
def get_settings() -> AudioSettings:
    return AudioSettings()
