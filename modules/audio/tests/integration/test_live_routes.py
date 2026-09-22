"""The socket: who gets in, what comes out, and what is never left behind.

The transcriber is faked. The route is mounted the way apps/api mounts it,
with the session dependency pointed at the test transaction.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from autune_audio.config import AudioSettings
from autune_audio.live import registry
from autune_audio.live import routes as live_routes
from autune_audio.live.segmenter import Segmenter
from autune_audio.live.session import LiveSession
from autune_audio.live.transcriber import Transcriber
from autune_audio.router import router
from autune_audio.schemas import SAMPLE_RATE, Transcription, Waveform, Word
from autune_audio.schemas import Segment as WhisperSegment
from autune_core import Meeting, TeamMember, User
from autune_core.auth import issue_token
from autune_core.errors import ConfigurationError

FRAME = SAMPLE_RATE // 5


def pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def tone(ms: int) -> np.ndarray:
    t = np.arange(SAMPLE_RATE * ms // 1000, dtype=np.float32) / SAMPLE_RATE
    return (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(ms: int) -> np.ndarray:
    return np.zeros(SAMPLE_RATE * ms // 1000, dtype=np.float32)


def energy(frame: np.ndarray) -> float:
    return float(min(1.0, np.sqrt(np.mean(frame * frame)) * 4))


def saying(text: str, *, fail_first: bool = False) -> Transcriber:
    calls = 0

    def transcribe(waveform: Waveform) -> Transcription:
        nonlocal calls
        calls += 1
        if fail_first and calls == 1:
            raise RuntimeError("model fell over")
        words = tuple(
            Word(start=0.0, end=waveform.duration, text=p, probability=0.9) for p in text.split()
        )
        return Transcription(
            segments=(WhisperSegment(start=0.0, end=waveform.duration, text=text, words=words),),
            language="ko",
            language_probability=1.0,
            duration=waveform.duration,
        )

    return Transcriber(transcribe=transcribe, warm_up=lambda: None)


@pytest.fixture
def member(db_session: Session, team: str) -> User:
    user = User(email="member@example.com", display_name="팀원")
    db_session.add(user)
    db_session.flush()
    db_session.add(TeamMember(team_id=team, user_id=user.id))
    db_session.flush()
    return user


@pytest.fixture
def outsider(db_session: Session) -> User:
    user = User(email="outsider@example.com", display_name="남")
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def temp_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(live_routes, "get_settings", lambda: AudioSettings(temp_dir=str(scratch)))
    return scratch


@pytest.fixture
def client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, temp_dir: Path
) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router, prefix="/api/audio")

    # The route opens its own session per connection (a socket outlives a
    # request). Point that at the test transaction.
    from contextlib import contextmanager

    @contextmanager
    def scope():
        # Mirrors ``autune_core.db.session_scope``'s commit/rollback-on-
        # exception semantics on a SAVEPOINT scoped to this call, so a
        # failure inside it rolls back only what this scope did (the
        # ``recording`` flip) -- not the fixtures already flushed into the
        # test's own outer savepoint before this connection's hello ran.
        with db_session.begin_nested():
            yield db_session

    monkeypatch.setattr(live_routes, "session_scope", scope)
    monkeypatch.setattr(
        live_routes,
        "build_session",
        lambda: LiveSession(
            segmenter=Segmenter(speech_probability=energy, min_silence_ms=700),
            transcriber=saying("연락처는 010-1234-5678입니다"),
        ),
    )
    registry.clear()
    yield TestClient(app)
    registry.clear()


def connect(client: TestClient, meeting: str):
    return client.websocket_connect(f"/api/audio/live/{meeting}")


def hello(ws, token: str) -> None:
    ws.send_text(json.dumps({"type": "hello", "token": token}))


def close_code(ws) -> int:
    with pytest.raises(WebSocketDisconnect) as caught:
        ws.receive_text()
    return caught.value.code


def test_no_token_is_4401(client: TestClient, meeting: str) -> None:
    with connect(client, meeting) as ws:
        ws.send_text(json.dumps({"type": "hello", "token": "nope"}))
        assert close_code(ws) == 4401


def test_a_message_before_hello_is_4401(client: TestClient, meeting: str) -> None:
    with connect(client, meeting) as ws:
        ws.send_text(json.dumps({"type": "pause"}))
        assert close_code(ws) == 4401


def test_an_outsider_is_4403(client: TestClient, meeting: str, outsider: User) -> None:
    with connect(client, meeting) as ws:
        hello(ws, issue_token(outsider.id))
        assert close_code(ws) == 4403


def test_a_missing_meeting_is_4404(client: TestClient, member: User) -> None:
    with connect(client, "mtg_nope") as ws:
        hello(ws, issue_token(member.id))
        assert close_code(ws) == 4404


def test_a_meeting_past_recording_is_4410_not_4409(
    client: TestClient, meeting: str, member: User, db_session: Session
) -> None:
    """The first microphone run of the day: a meeting that had already gone
    through stop and upload answered "someone else is recording". Nobody
    was; the meeting was complete, and that is its own refusal."""
    db_session.get(Meeting, meeting).status = "complete"
    db_session.flush()
    with connect(client, meeting) as ws:
        hello(ws, issue_token(member.id))
        assert close_code(ws) == 4410
    assert registry.open_count() == 0


def test_a_second_session_on_the_same_meeting_is_4409(
    client: TestClient, meeting: str, member: User
) -> None:
    with connect(client, meeting) as first:
        hello(first, issue_token(member.id))
        assert first.receive_json() == {"type": "ready"}
        with connect(client, meeting) as second:
            hello(second, issue_token(member.id))
            assert close_code(second) == 4409


def test_an_utterance_comes_back_as_a_masked_row_and_the_meeting_is_recording(
    client: TestClient, meeting: str, member: User, db_session: Session
) -> None:
    with connect(client, meeting) as ws:
        hello(ws, issue_token(member.id))
        assert ws.receive_json() == {"type": "ready"}
        assert db_session.get(Meeting, meeting).status == "recording"

        audio = np.concatenate([tone(1000), silence(1000)])
        for i in range(0, len(audio) - len(audio) % FRAME, FRAME):
            ws.send_bytes(pcm(audio[i : i + FRAME]))

        message = ws.receive_json()
        assert message["type"] == "row"
        assert message["utterance"]["text"] == "연락처는 010-****-5678입니다"
        assert message["utterance"]["speaker_id"] is None
        assert message["utterance"]["id"].startswith("utt_live_")


def test_stop_flushes_the_last_utterance_then_ends(
    client: TestClient, meeting: str, member: User, db_session: Session
) -> None:
    with connect(client, meeting) as ws:
        hello(ws, issue_token(member.id))
        ws.receive_json()
        audio = tone(1000)
        for i in range(0, len(audio) - len(audio) % FRAME, FRAME):
            ws.send_bytes(pcm(audio[i : i + FRAME]))

        ws.send_text(json.dumps({"type": "stop"}))

        assert ws.receive_json()["type"] == "row"
        assert ws.receive_json() == {"type": "ended"}
    assert db_session.get(Meeting, meeting).status == "recording"


def test_nothing_touches_the_disk(
    client: TestClient, meeting: str, member: User, temp_dir: Path
) -> None:
    with connect(client, meeting) as ws:
        hello(ws, issue_token(member.id))
        ws.receive_json()
        audio = np.concatenate([tone(1000), silence(1000)])
        for i in range(0, len(audio) - len(audio) % FRAME, FRAME):
            ws.send_bytes(pcm(audio[i : i + FRAME]))
        ws.receive_json()
        ws.send_text(json.dumps({"type": "stop"}))
        ws.receive_json()
    assert list(temp_dir.iterdir()) == []


def test_the_text_is_not_in_the_log(
    client: TestClient, meeting: str, member: User, capsys: pytest.CaptureFixture[str]
) -> None:
    # ``autune_core.get_logger`` is structlog with ``PrintLoggerFactory``,
    # which writes straight to stdout -- not through stdlib ``logging``, so
    # ``caplog`` never sees it. ``capsys`` reads the same stream the process
    # actually writes to.
    with connect(client, meeting) as ws:
        hello(ws, issue_token(member.id))
        ws.receive_json()
        audio = np.concatenate([tone(1000), silence(1000)])
        for i in range(0, len(audio) - len(audio) % FRAME, FRAME):
            ws.send_bytes(pcm(audio[i : i + FRAME]))
        ws.receive_json()
    out = capsys.readouterr().out
    assert "1234" not in out
    assert "연락처" not in out


def test_a_failed_segment_is_one_error_and_the_next_is_normal(
    client: TestClient, meeting: str, member: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        live_routes,
        "build_session",
        lambda: LiveSession(
            segmenter=Segmenter(speech_probability=energy, min_silence_ms=700),
            transcriber=saying("두 번째", fail_first=True),
        ),
    )
    with connect(client, meeting) as ws:
        hello(ws, issue_token(member.id))
        ws.receive_json()
        audio = np.concatenate([tone(1000), silence(1000)])
        for _ in range(2):
            for i in range(0, len(audio) - len(audio) % FRAME, FRAME):
                ws.send_bytes(pcm(audio[i : i + FRAME]))

        assert ws.receive_json() == {"type": "error", "code": "transcribe_failed"}
        assert ws.receive_json()["utterance"]["text"] == "두 번째"


def test_an_oversized_frame_is_refused_and_the_session_continues(
    client: TestClient, meeting: str, member: User
) -> None:
    with connect(client, meeting) as ws:
        hello(ws, issue_token(member.id))
        ws.receive_json()
        ws.send_bytes(b"\x00" * (32 * 1024 + 2))
        assert ws.receive_json() == {"type": "error", "code": "frame_too_large"}
        ws.send_text(json.dumps({"type": "stop"}))
        assert ws.receive_json() == {"type": "ended"}


def test_the_registry_is_empty_after_stop(client: TestClient, meeting: str, member: User) -> None:
    with connect(client, meeting) as ws:
        hello(ws, issue_token(member.id))
        ws.receive_json()
        ws.send_text(json.dumps({"type": "stop"}))
        ws.receive_json()
    assert registry.open_count() == 0


def test_a_model_that_cannot_load_is_4503_and_leaves_no_registry_entry(
    client: TestClient, meeting: str, member: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_warm_up() -> None:
        raise RuntimeError("no token")

    monkeypatch.setattr(
        live_routes,
        "build_session",
        lambda: LiveSession(
            segmenter=Segmenter(speech_probability=energy, min_silence_ms=700),
            transcriber=Transcriber(warm_up=broken_warm_up),
        ),
    )
    with connect(client, meeting) as ws:
        hello(ws, issue_token(member.id))
        assert ws.receive_json() == {"type": "error", "code": "model_unavailable"}
        assert close_code(ws) == 4503
    assert registry.open_count() == 0


def test_a_session_that_cannot_be_built_is_4503_and_the_meeting_stays_scheduled(
    client: TestClient,
    meeting: str,
    member: User,
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    def broken() -> LiveSession:
        raise ConfigurationError("AUTUNE_AUDIO_LIVE_TRANSCRIBER_IMPL")

    monkeypatch.setattr(live_routes, "build_session", broken)
    with connect(client, meeting) as ws:
        hello(ws, issue_token(member.id))
        assert close_code(ws) == 4503
    assert db_session.get(Meeting, meeting).status == "scheduled"
    assert not registry.is_open(meeting)


def test_no_hello_within_the_timeout_is_4401(
    client: TestClient, meeting: str, monkeypatch: pytest.MonkeyPatch, temp_dir: Path
) -> None:
    monkeypatch.setattr(
        live_routes,
        "get_settings",
        lambda: AudioSettings(temp_dir=str(temp_dir), live_hello_timeout_s=0.2),
    )
    with connect(client, meeting) as ws:
        assert close_code(ws) == 4401
    assert registry.open_count() == 0


def test_a_binary_first_frame_is_4401(client: TestClient, meeting: str) -> None:
    with connect(client, meeting) as ws:
        ws.send_bytes(b"\x00" * 64)
        assert close_code(ws) == 4401


def test_the_session_ends_at_the_limit_and_releases_the_meeting(
    client: TestClient, meeting: str, member: User, monkeypatch: pytest.MonkeyPatch, temp_dir: Path
) -> None:
    monkeypatch.setattr(
        live_routes,
        "get_settings",
        lambda: AudioSettings(temp_dir=str(temp_dir), live_max_session_s=0.5),
    )
    with connect(client, meeting) as ws:
        hello(ws, issue_token(member.id))
        assert ws.receive_json() == {"type": "ready"}
        audio = tone(600)
        for i in range(0, len(audio) - len(audio) % FRAME, FRAME):
            ws.send_bytes(pcm(audio[i : i + FRAME]))

        # The open utterance is flushed as a row before ``ended``; whether it
        # is depends on how much the segmenter held when the limit struck.
        message = ws.receive_json()
        while message["type"] == "row":
            message = ws.receive_json()
        assert message == {"type": "ended"}
        assert close_code(ws) == 1000
    assert registry.open_count() == 0


def test_a_client_that_disconnects_mid_session_releases_the_meeting(
    client: TestClient, meeting: str, member: User
) -> None:
    with connect(client, meeting) as ws:
        hello(ws, issue_token(member.id))
        assert ws.receive_json() == {"type": "ready"}
        assert registry.is_open(meeting)
    # The handler runs on the test client's portal; give it a tick to see
    # the disconnect if it has not already.
    deadline = time.monotonic() + 2.0
    while registry.is_open(meeting) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert registry.open_count() == 0
