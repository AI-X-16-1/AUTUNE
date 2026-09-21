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

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from autune_audio import service
from autune_audio.config import get_settings
from autune_audio.live import protocol
from autune_audio.live.segmenter import Segmenter
from autune_audio.live.session import LiveSession, TranscribeFailed
from autune_audio.live.transcriber import Transcriber
from autune_core import get_logger
from autune_core.db import session_scope
from autune_core.errors import ConflictError, NotFoundError, PermissionDeniedError

log = get_logger(__name__)

router = APIRouter()

_transcriber = Transcriber()
_live: dict[str, LiveSession] = {}
"""Open sessions by meeting id. One per meeting; a second hello is refused."""


def build_session() -> LiveSession:
    """A fresh session on the process-wide transcriber. Tests replace this."""
    return LiveSession(segmenter=Segmenter(), transcriber=_transcriber)


@router.websocket("/live/{meeting_id}")
async def live(websocket: WebSocket, meeting_id: str) -> None:
    settings = get_settings()
    await websocket.accept()

    # --- hello: authenticate, begin, claim --------------------------------
    # The claim (``_live[meeting_id] = session``) happens right after
    # ``begin_live`` succeeds, with no ``await`` anywhere between the
    # membership check and the claim: this whole block is synchronous, so no
    # other connection's hello can interleave and see a stale "not live yet".
    # Claiming only once ``begin_live`` has succeeded also means a meeting
    # that ``begin_live`` itself refuses (wrong status) was never claimed and
    # needs no rollback.
    try:
        with anyio.fail_after(settings.live_hello_timeout_s):
            first = await websocket.receive_text()
        message = protocol.parse_client(first)
        if not isinstance(message, protocol.Hello):
            raise protocol.ProtocolError("hello_expected")
        with session_scope() as db:
            service.authenticate_live(db, token=message.token, meeting_id=meeting_id)
            if meeting_id in _live:
                raise ConflictError("a live session is already open for this meeting")
            service.begin_live(db, meeting_id=meeting_id)
            session = build_session()
            _live[meeting_id] = session
    except service.NotATeamMemberError as exc:
        # A real user, just not one this meeting's team recognises --
        # authenticated, not let in.
        log.info("live_refused", meeting_id=meeting_id, reason=type(exc).__name__)
        await websocket.close(code=protocol.NOT_A_MEMBER)
        return
    except (TimeoutError, protocol.ProtocolError, PermissionDeniedError) as exc:
        # A stalled hello, an unparsable first message, and a token naming
        # nobody all close the same way: the client never told us who it is.
        # The reason is logged by type only.
        log.info("live_refused", meeting_id=meeting_id, reason=type(exc).__name__)
        await websocket.close(code=protocol.UNAUTHENTICATED)
        return
    except NotFoundError as exc:
        log.info("live_refused", meeting_id=meeting_id, reason=type(exc).__name__)
        await websocket.close(code=protocol.NO_SUCH_MEETING)
        return
    except ConflictError as exc:
        log.info("live_refused", meeting_id=meeting_id, reason=type(exc).__name__)
        await websocket.close(code=protocol.ALREADY_LIVE)
        return
    except WebSocketDisconnect:
        return

    # --- warm-up, ready, and the loop, all covered by the same claim -----
    # Everything from here on holds the ``_live[meeting_id]`` claim taken
    # above, and one ``finally`` releases it -- whether warm-up fails,
    # ``ready`` never reaches a client that already left, the loop ends
    # normally, or the socket drops mid-session.
    reason = "closed"
    try:
        try:
            await session.warm_up()
        except Exception as exc:
            log.warning("live_model_unavailable", error=type(exc).__name__)
            await websocket.send_json(protocol.error("model_unavailable"))
            await websocket.close(code=protocol.MODEL_UNAVAILABLE)
            reason = "model_unavailable"
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
                    await _finish(websocket, session)
                    reason = "stopped"
        if deadline.cancelled_caught:
            reason = "session_limit"
            await _finish(websocket, session)
    except WebSocketDisconnect:
        reason = "disconnected"
    finally:
        _live.pop(meeting_id, None)
        log.info(
            "live_session_closed", meeting_id=meeting_id, rows=session.rows_sent, reason=reason
        )


async def _emit(websocket: WebSocket, session: LiveSession, data: bytes) -> None:
    try:
        rows = await session.on_frame(data)
    except TranscribeFailed:
        await websocket.send_json(protocol.error("transcribe_failed"))
        return
    for row in rows:
        await websocket.send_json(protocol.row(row))


async def _finish(websocket: WebSocket, session: LiveSession) -> None:
    try:
        rows = await session.stop()
    except TranscribeFailed:
        rows = []
        await websocket.send_json(protocol.error("transcribe_failed"))
    for row in rows:
        await websocket.send_json(protocol.row(row))
    await websocket.send_json(protocol.ended())
    await websocket.close()
