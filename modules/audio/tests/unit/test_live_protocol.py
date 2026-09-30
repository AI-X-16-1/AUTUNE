from __future__ import annotations

import pytest

from autune_audio.live import protocol
from autune_contracts.transcript import Utterance


def test_hello_carries_the_token() -> None:
    message = protocol.parse_client('{"type": "hello", "token": "abc"}')
    assert isinstance(message, protocol.Hello)
    assert message.token == "abc"


def test_a_hello_without_a_token_parses() -> None:
    """A cookie-signed-in browser has no token to send; the route reads the
    handshake's session cookie instead (test_live_routes)."""
    message = protocol.parse_client('{"type": "hello"}')
    assert isinstance(message, protocol.Hello)
    assert message.token is None


@pytest.mark.parametrize("kind", ["pause", "resume", "stop"])
def test_control_messages_parse(kind: str) -> None:
    message = protocol.parse_client(f'{{"type": "{kind}"}}')
    assert isinstance(message, protocol.Control)
    assert message.type == kind


@pytest.mark.parametrize(
    "text",
    ['{"type": "dance"}', "not json", '{"token": "abc"}'],
)
def test_anything_else_is_a_protocol_error(text: str) -> None:
    with pytest.raises(protocol.ProtocolError) as caught:
        protocol.parse_client(text)
    assert caught.value.code == "bad_message"


def test_a_row_is_a_contract_utterance() -> None:
    utterance = Utterance(
        id="utt_live_1",
        speaker="?",
        speaker_id=None,
        role=None,
        start=0.0,
        end=1.5,
        text="네",
        confidence=0.9,
    )
    message = protocol.row(utterance)
    assert message["type"] == "row"
    assert Utterance.model_validate(message["utterance"]) == utterance


def test_the_other_server_messages_have_a_type() -> None:
    assert protocol.ready() == {"type": "ready"}
    assert protocol.ended() == {"type": "ended"}
    assert protocol.error("transcribe_failed") == {"type": "error", "code": "transcribe_failed"}
