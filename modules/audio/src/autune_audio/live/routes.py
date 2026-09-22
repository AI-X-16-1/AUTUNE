"""The socket. Hello, the loop, the close codes, one session per meeting.

Everything that touches audio is in ``session.py``; everything that decides
who may connect is in ``service.py``. This file sequences them and turns
their refusals into close codes.

The route opens its own database session per connection rather than taking
``get_session`` as a dependency: a socket outlives a request, and a session
held for three hours across ``await``s is a session that will be stale when
it is finally used. Each database touch here is short and scoped.
"""

from __future__ import annotations

from contextlib import suppress

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from autune_audio import service
from autune_audio.config import get_settings
from autune_audio.live import protocol, registry
from autune_audio.live.segmenter import Segmenter
from autune_audio.live.session import LiveSession, TranscribeFailed
from autune_audio.live.transcriber import Transcriber
from autune_core import get_logger
from autune_core.db import session_scope
from autune_core.errors import (
    ConfigurationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)

log = get_logger(__name__)

router = APIRouter()


class _AlreadyLiveError(ConflictError):
    """A second hello for a meeting this process is already recording."""


_transcriber: Transcriber | None = None
"""Built by the first session, not at import: ``Transcriber()`` resolves the
engine from settings, and a value that cannot run here (``mlx`` off Apple
silicon) must refuse one socket with 4503, not stop the API from starting
(``ConfigurationError``: "at the point of use, not at import")."""


def shared_transcriber() -> Transcriber:
    global _transcriber  # noqa: PLW0603 - one model per process, by design
    if _transcriber is None:
        _transcriber = Transcriber()
    return _transcriber


def build_session() -> LiveSession:
    """A fresh session on the process-wide transcriber. Tests replace this."""
    return LiveSession(
        segmenter=Segmenter(min_silence_ms=get_settings().live_min_silence_ms),
        transcriber=shared_transcriber(),
    )


@router.websocket("/live/{meeting_id}")
async def live(websocket: WebSocket, meeting_id: str) -> None:
    settings = get_settings()
    await websocket.accept()

    # --- hello: authenticate, begin, claim --------------------------------
    # The claim (``registry.claim(meeting_id, session)``) happens right after
    # the database scope has committed, with no ``await`` anywhere between
    # the "already live" check and the claim: this whole block is
    # synchronous, so no other connection's hello can interleave and see a
    # stale "not live yet". Claiming only once the scope has closed also
    # means a meeting that ``begin_live`` refuses (wrong status), or whose
    # commit fails, was never claimed and needs no rollback -- a claim taken
    # inside the scope would outlive a commit failure, which raises past
    # every handler below.
    try:
        with anyio.fail_after(settings.live_hello_timeout_s):
            event = await websocket.receive()
        if event["type"] == "websocket.disconnect":
            return
        first = event.get("text")
        if first is None:
            # Audio before hello. The bytes are not looked at.
            raise protocol.ProtocolError("hello_expected")
        message = protocol.parse_client(first)
        if not isinstance(message, protocol.Hello):
            raise protocol.ProtocolError("hello_expected")
        with session_scope() as db:
            service.authenticate_live(db, token=message.token, meeting_id=meeting_id)
            if registry.is_open(meeting_id):
                raise _AlreadyLiveError("a live session is already open for this meeting")
            service.begin_live(db, meeting_id=meeting_id)
            # Inside the scope on purpose: a session that cannot be built
            # (engine misconfigured) raises here and the scope rolls
            # ``recording`` back with it. Nothing below awaits before the
            # claim, so the atomicity comment above still holds.
            session = build_session()
        registry.claim(meeting_id, session)
    except service.NotATeamMemberError as exc:
        # A real user, just not one this meeting's team recognises --
        # authenticated, not let in.
        await _refuse(
            websocket, protocol.NOT_A_MEMBER, meeting_id=meeting_id, reason=type(exc).__name__
        )
        return
    except (TimeoutError, protocol.ProtocolError, PermissionDeniedError) as exc:
        # A stalled hello, an unparsable first message, and a token naming
        # nobody all close the same way: the client never told us who it is.
        # The reason is logged by type only.
        await _refuse(
            websocket, protocol.UNAUTHENTICATED, meeting_id=meeting_id, reason=type(exc).__name__
        )
        return
    except NotFoundError as exc:
        await _refuse(
            websocket, protocol.NO_SUCH_MEETING, meeting_id=meeting_id, reason=type(exc).__name__
        )
        return
    except _AlreadyLiveError as exc:
        await _refuse(
            websocket, protocol.ALREADY_LIVE, meeting_id=meeting_id, reason=type(exc).__name__
        )
        return
    except ConflictError as exc:
        # begin_live: the meeting is analysing, complete or delivered. A
        # different message for the person than "someone else is recording".
        await _refuse(
            websocket, protocol.NOT_RECORDABLE, meeting_id=meeting_id, reason=type(exc).__name__
        )
        return
    except ConfigurationError as exc:
        # The engine this deployment asked for cannot run here. The status
        # flip was rolled back with the scope; refuse like a model that
        # failed to load, and say so in the log by type.
        log.warning("live_model_unavailable", error=type(exc).__name__)
        with suppress(WebSocketDisconnect):
            await websocket.send_json(protocol.error("model_unavailable"))
        await _refuse(
            websocket, protocol.MODEL_UNAVAILABLE, meeting_id=meeting_id, reason=type(exc).__name__
        )
        return
    except WebSocketDisconnect:
        return

    # --- warm-up, ready, and the loop, all covered by the same claim -----
    # Everything from here on holds the registry claim taken above, and one
    # ``finally`` releases it -- whether warm-up fails,
    # ``ready`` never reaches a client that already left, the loop ends
    # normally, or the socket drops mid-session.
    reason = "closed"
    try:
        try:
            await session.warm_up()
        except Exception as exc:
            log.warning("live_model_unavailable", error=type(exc).__name__)
            reason = "model_unavailable"
            await websocket.send_json(protocol.error("model_unavailable"))
            await _refuse(
                websocket, protocol.MODEL_UNAVAILABLE, meeting_id=meeting_id, reason=reason
            )
            return

        log.info("live_session_opened", meeting_id=meeting_id)
        await websocket.send_json(protocol.ready())

        with anyio.move_on_after(settings.live_max_session_s) as deadline:
            while session.state != "ended":
                event = await websocket.receive()
                if event["type"] == "websocket.disconnect":
                    reason = "disconnected"
                    return
                if (data := event.get("bytes")) is not None:
                    if len(data) > settings.live_max_frame_bytes:
                        await websocket.send_json(protocol.error("frame_too_large"))
                        continue
                    await _emit(websocket, session, data)
                    continue
                try:
                    control = protocol.parse_client(event.get("text") or "")
                except protocol.ProtocolError as exc:
                    await websocket.send_json(protocol.error(exc.code))
                    continue
                if isinstance(control, protocol.Hello):
                    await websocket.send_json(protocol.error("already_said_hello"))
                elif control.type == "pause":
                    session.pause()
                elif control.type == "resume":
                    session.resume()
                else:
                    await _finish(websocket, session, meeting_id=meeting_id)
                    reason = "stopped"
        if deadline.cancelled_caught:
            reason = "session_limit"
            await _finish(websocket, session, meeting_id=meeting_id)
    except WebSocketDisconnect:
        reason = "disconnected"
    finally:
        registry.release(meeting_id)
        log.info(
            "live_session_closed", meeting_id=meeting_id, rows=session.rows_sent, reason=reason
        )


async def _refuse(websocket: WebSocket, code: int, *, meeting_id: str, reason: str) -> None:
    """Log why and close with ``code``. A client that has already gone is not
    an error: there is nobody left to refuse."""
    log.info("live_refused", meeting_id=meeting_id, reason=reason)
    with suppress(WebSocketDisconnect):
        await websocket.close(code=code)


async def _emit(websocket: WebSocket, session: LiveSession, data: bytes) -> None:
    try:
        rows = await session.on_frame(data)
    except TranscribeFailed:
        await websocket.send_json(protocol.error("transcribe_failed"))
        return
    for row in rows:
        await websocket.send_json(protocol.row(row))


async def _finish(websocket: WebSocket, session: LiveSession, *, meeting_id: str) -> None:
    try:
        rows = await session.stop()
    except TranscribeFailed:
        rows = []
        await websocket.send_json(protocol.error("transcribe_failed"))
    for row in rows:
        await websocket.send_json(protocol.row(row))
    # Released before ``ended``: the browser uploads the moment it sees
    # ``ended``, and ``start_transcription`` refuses while the claim is held.
    registry.release(meeting_id)
    await websocket.send_json(protocol.ended())
    await websocket.close()
