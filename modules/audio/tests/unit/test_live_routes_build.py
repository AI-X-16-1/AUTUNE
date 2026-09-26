"""What the route hands a new session, and when it builds the models.

The live transcriber is built when the first session needs it, not when the
router is imported: a misconfigured engine must not take the API down. The
embedder is one per process and the tracker one per session, shaped by the
settings. Nothing here opens a socket or loads a model."""

from __future__ import annotations

import subprocess
import sys

import pytest

from autune_audio.config import AudioSettings
from autune_audio.live import backends, registry, routes
from autune_audio.live.embedder import Embedder
from autune_core.errors import ConfigurationError


@pytest.fixture(autouse=True)
def fresh_transcriber(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_transcriber", None)


def test_importing_the_router_with_a_misconfigured_engine_does_not_raise() -> None:
    """The PR #307 finding: ``mlx`` on a non-Apple machine must refuse one
    socket, not stop the API from importing module A's router."""
    code = (
        "import os; os.environ['AUTUNE_AUDIO_LIVE_TRANSCRIBER_IMPL'] = 'mlx'\n"
        "from autune_audio.live import backends, routes\n"
        "backends.mlx_available = lambda: False\n"
        "raise SystemExit(0 if routes._transcriber is None else 1)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=120)


def test_a_misconfigured_engine_fails_at_the_first_session_not_at_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backends, "mlx_available", lambda: False)
    monkeypatch.setattr(
        backends, "get_settings", lambda: AudioSettings(live_transcriber_impl="mlx")
    )
    with pytest.raises(ConfigurationError):
        routes.build_session()
    assert routes._transcriber is None  # noqa: SLF001 - nothing half-built is kept


def test_sessions_share_one_transcriber(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backends, "mlx_available", lambda: False)
    monkeypatch.setattr(
        backends, "get_settings", lambda: AudioSettings(live_transcriber_impl="faster_whisper")
    )
    a = routes.build_session()
    b = routes.build_session()
    assert a._transcriber is b._transcriber is routes._transcriber  # noqa: SLF001


@pytest.mark.asyncio
async def test_the_claim_is_released_before_ended_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.clear()
    order: list[str] = []

    class Socket:
        async def send_json(self, data: dict) -> None:
            order.append(data["type"])

        async def close(self, code: int = 1000) -> None:
            order.append("close")

    class Session:
        rows_sent = 0

        async def stop(self):  # noqa: ANN202
            return []

    registry.claim("m1", Session())  # type: ignore[arg-type]
    real_release = registry.release

    def release(meeting_id: str) -> None:
        order.append("release")
        real_release(meeting_id)

    monkeypatch.setattr(registry, "release", release)
    await routes._finish(Socket(), Session(), meeting_id="m1")  # noqa: SLF001
    assert order == ["release", "ended", "close"]


def test_the_tracker_takes_the_stored_paths_speaker_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: AudioSettings(
            diarization_num_speakers=1, live_speaker_threshold=0.5, live_speaker_min_s=0.8
        ),
    )
    session = routes.build_session()
    assert session.tracker.max_speakers == 1
    assert session.tracker.threshold == 0.5


def test_an_upper_bound_serves_when_there_is_no_exact_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "get_settings", lambda: AudioSettings(diarization_max_speakers=3))
    assert routes.build_session().tracker.max_speakers == 3


def test_no_hint_is_no_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "get_settings", lambda: AudioSettings())
    assert routes.build_session().tracker.max_speakers is None


def test_sessions_share_one_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "get_settings", lambda: AudioSettings())
    a = routes.build_session()
    b = routes.build_session()
    assert isinstance(routes._embedder, Embedder)  # noqa: SLF001 - the seam under test
    assert a._embedder is b._embedder is routes._embedder  # noqa: SLF001
    assert a.tracker is not b.tracker
