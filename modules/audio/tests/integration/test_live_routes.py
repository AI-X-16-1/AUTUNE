"""The socket: who gets in, what comes out, and what is never left behind.

The transcriber is faked. The route is mounted the way apps/api mounts it,
with the session dependency pointed at the test transaction.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from autune_audio.config import AudioSettings
from autune_audio.live import routes as live_routes
from autune_audio.live.segmenter import Segmenter
from autune_audio.live.session import LiveSession
from autune_audio.live.transcriber import Transcriber
from autune_audio.router import router
from autune_audio.schemas import SAMPLE_RATE, Transcription, Waveform, Word
from autune_audio.schemas import Segment as WhisperSegment
from autune_core import Meeting, TeamMember, User
from autune_core.auth import issue_token

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
        yield db_session

    monkeypatch.setattr(live_routes, "session_scope", scope)
    monkeypatch.setattr(
        live_routes,
        "build_session",
        lambda: LiveSession(
            segmenter=Segmenter(speech_probability=energy),
            transcriber=saying("연락처는 010-1234-5678입니다"),
        ),
    )
    live_routes._live.clear()
    yield TestClient(app)
    live_routes._live.clear()


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
    client: TestClient, meeting: str, member: User, caplog: pytest.LogCaptureFixture
) -> None:
    with connect(client, meeting) as ws:
        hello(ws, issue_token(member.id))
        ws.receive_json()
        audio = np.concatenate([tone(1000), silence(1000)])
        for i in range(0, len(audio) - len(audio) % FRAME, FRAME):
            ws.send_bytes(pcm(audio[i : i + FRAME]))
        ws.receive_json()
    assert "1234" not in caplog.text
    assert "연락처" not in caplog.text


def test_a_failed_segment_is_one_error_and_the_next_is_normal(
    client: TestClient, meeting: str, member: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        live_routes,
        "build_session",
        lambda: LiveSession(
            segmenter=Segmenter(speech_probability=energy),
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
