"""The one-session-per-meeting claim, in a module both the route and the
service can import."""

from __future__ import annotations

import subprocess
import sys

import pytest

from autune_audio.live import registry


@pytest.fixture(autouse=True)
def empty() -> None:
    registry.clear()


def test_claim_release_is_open() -> None:
    assert not registry.is_open("m1")
    registry.claim("m1", object())  # type: ignore[arg-type]
    assert registry.is_open("m1")
    registry.release("m1")
    assert not registry.is_open("m1")


def test_release_is_idempotent() -> None:
    registry.release("never-claimed")
    registry.claim("m1", object())  # type: ignore[arg-type]
    registry.release("m1")
    registry.release("m1")
    assert registry.open_count() == 0


def test_a_second_claim_on_the_same_meeting_is_refused() -> None:
    registry.claim("m1", object())  # type: ignore[arg-type]
    with pytest.raises(registry.AlreadyOpenError):
        registry.claim("m1", object())  # type: ignore[arg-type]


def test_importing_service_does_not_pull_in_the_route_stack() -> None:
    """The worker imports ``autune_audio.service`` (through ``autune_audio.tasks``)
    to refuse an upload while a live socket is open, but the websocket route
    module (``live.routes``) must stay out of the worker's import graph: that is
    the fix for the ``service -> live/__init__ -> routes -> service`` cycle,
    proven rather than left as an unwritten rule."""
    code = (
        "import sys\n"
        "import autune_audio.service\n"
        "raise SystemExit(0 if 'autune_audio.live.routes' not in sys.modules else 1)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=120)
