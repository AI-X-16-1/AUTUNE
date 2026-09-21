"""What crosses the socket, in one place.

Text frames are JSON and described here; binary frames are PCM16 audio and
never touch this module. Design, section 2.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from autune_contracts.transcript import Utterance

# Close codes. 4xxx is the range an application may use.
UNAUTHENTICATED = 4401
NOT_A_MEMBER = 4403
NO_SUCH_MEETING = 4404
ALREADY_LIVE = 4409
MODEL_UNAVAILABLE = 4503


class ProtocolError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Hello(BaseModel):
    type: Literal["hello"]
    token: str


class Control(BaseModel):
    type: Literal["pause", "resume", "stop"]


ClientMessage = Hello | Control


def parse_client(text: str) -> ClientMessage:
    """One message from the browser. Anything unrecognisable is one error code
    -- the browser sent something this server does not speak."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError("bad_message") from exc
    if not isinstance(data, dict):
        raise ProtocolError("bad_message")
    try:
        if data.get("type") == "hello":
            return Hello.model_validate(data)
        return Control.model_validate(data)
    except ValidationError as exc:
        raise ProtocolError("bad_message") from exc


def ready() -> dict[str, Any]:
    return {"type": "ready"}


def row(utterance: Utterance) -> dict[str, Any]:
    return {"type": "row", "utterance": utterance.model_dump(mode="json")}


def error(code: str) -> dict[str, Any]:
    return {"type": "error", "code": code}


def ended() -> dict[str, Any]:
    return {"type": "ended"}
