"""The live transcriber is built when the first session needs it, not when
the router is imported: a misconfigured engine must not take the API down."""

from __future__ import annotations

import pytest

from autune_audio.config import AudioSettings
from autune_audio.live import backends, routes
from autune_core.errors import ConfigurationError


@pytest.fixture(autouse=True)
def fresh_transcriber(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_transcriber", None)


def test_importing_the_router_builds_no_transcriber() -> None:
    assert routes._transcriber is None  # noqa: SLF001 - the seam under test


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
