"""``GmailClient`` against Gmail's answers (#552), over a mock transport -- no
network calls."""

from __future__ import annotations

import base64
import email
import json
from collections.abc import Callable
from email.policy import default

import httpx
import pytest

from autune_core.errors import PrivacyViolationError
from autune_integrations.errors import PermanentIntegrationError
from autune_integrations.gmail import GmailClient

LINK = "https://autune.example/invite#abc-010-1234-5678-xyz"
"""A token Autune made, with a run of digits that reads as a phone number."""


def client(handler: Callable[[httpx.Request], httpx.Response]) -> GmailClient:
    c = GmailClient("token")
    c._client = httpx.Client(
        base_url="https://gmail.googleapis.com/gmail/v1", transport=httpx.MockTransport(handler)
    )
    return c


def test_send_posts_one_message_from_the_grants_own_account() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "msg-1"})

    sent = client(handler).send(
        to="newcomer@example.com",
        subject="Autune 팀 초대",
        body=f"아래 링크로 수락해 주세요.\n{LINK}\n",
        unchecked=[LINK],
    )

    assert sent == "msg-1"
    (request,) = seen
    assert request.url.path == "/gmail/v1/users/me/messages/send"
    raw = json.loads(request.content)["raw"]
    message = email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=default)
    assert message["To"] == "newcomer@example.com"
    assert message["Subject"] == "Autune 팀 초대"
    assert message["From"] is None  # Gmail sets the account's own address
    assert LINK in message.get_content()


def test_personal_data_in_the_text_is_refused_before_anything_is_sent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("nothing may be sent")

    with pytest.raises(PrivacyViolationError):
        client(handler).send(
            to="newcomer@example.com", subject="초대", body="제 번호는 010-1234-5678입니다."
        )


def test_only_the_values_named_unchecked_are_let_through() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("nothing may be sent")

    with pytest.raises(PrivacyViolationError):
        client(handler).send(
            to="newcomer@example.com",
            subject="초대",
            body=f"{LINK}\n연락처 010-9876-5432",
            unchecked=[LINK],
        )


def test_a_refusal_from_gmail_is_permanent_and_carries_its_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "insufficient scope"}})

    with pytest.raises(PermanentIntegrationError) as caught:
        client(handler).send(to="a@example.com", subject="초대", body="안녕하세요")

    assert caught.value.details["upstream_status"] == 403
    assert "insufficient" not in str(caught.value)
