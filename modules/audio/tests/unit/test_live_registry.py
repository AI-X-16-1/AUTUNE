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
    session = object()
    assert not registry.is_open("m1")
    registry.claim("m1", session, user_id="u1")  # type: ignore[arg-type]
    assert registry.is_open("m1")
    assert registry.holder("m1") == "u1"
    registry.release("m1", session)  # type: ignore[arg-type]
    assert not registry.is_open("m1")
    assert registry.holder("m1") is None


def test_release_is_idempotent() -> None:
    session = object()
    registry.release("never-claimed", session)  # type: ignore[arg-type]
    registry.claim("m1", session, user_id="u1")  # type: ignore[arg-type]
    registry.release("m1", session)  # type: ignore[arg-type]
    registry.release("m1", session)  # type: ignore[arg-type]
    assert registry.open_count() == 0


def test_a_second_claim_on_the_same_meeting_is_refused() -> None:
    registry.claim("m1", object(), user_id="u1")  # type: ignore[arg-type]
    with pytest.raises(registry.AlreadyOpenError):
        registry.claim("m1", object(), user_id="u1")  # type: ignore[arg-type]


def test_a_session_releases_only_its_own_claim() -> None:
    """#1019 review: an old socket's ``finally`` must not drop the claim a
    newer socket took for the same meeting after the old one let go."""
    old, new = object(), object()
    registry.claim("m1", old, user_id="u1")  # type: ignore[arg-type]
    registry.release("m1", old)  # type: ignore[arg-type]
    registry.claim("m1", new, user_id="u1")  # type: ignore[arg-type]
    registry.release("m1", old)  # type: ignore[arg-type]
    assert registry.is_open("m1")


def test_the_holder_can_give_the_claim_up_for_an_upload_and_nobody_else_can() -> None:
    registry.claim("m1", object(), user_id="u1")  # type: ignore[arg-type]
    assert not registry.give_up_for_upload("m1", user_id="u2")
    assert registry.is_open("m1")
    assert registry.give_up_for_upload("m1", user_id="u1")
    assert not registry.is_open("m1")


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
