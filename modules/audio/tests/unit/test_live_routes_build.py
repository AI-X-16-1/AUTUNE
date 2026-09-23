"""The live transcriber is built when the first session needs it, not when
the router is imported: a misconfigured engine must not take the API down."""

from __future__ import annotations

import subprocess
import sys

import pytest

from autune_audio.config import AudioSettings
from autune_audio.live import backends, registry, routes
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
