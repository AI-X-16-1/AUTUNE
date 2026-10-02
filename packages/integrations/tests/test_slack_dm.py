"""Direct messages reach a person's linked Slack account (#255), and Slack's
``ok: false`` is a failure, not a delivery (#280). Mock transport, no network."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from autune_core import user_integrations
from autune_integrations.errors import PermanentIntegrationError, SlackRecipientNotLinkedError
from autune_integrations.slack import SlackClient


def client(answer: dict[str, Any], sent: list[dict[str, Any]]) -> SlackClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=answer)

    c = SlackClient("xoxb-token")
    c._client = httpx.Client(
        base_url="https://slack.com/api", transport=httpx.MockTransport(handler)
    )
    return c


def test_a_dm_to_an_autune_user_goes_to_their_linked_slack_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(user_integrations, "slack_member_id", lambda uid: "U123")
    sent: list[dict[str, Any]] = []

    client({"ok": True, "ts": "1.2"}, sent).send_dm("user_abc", "확인 부탁드립니다")

    assert sent[0]["channel"] == "U123"


def test_someone_who_has_not_linked_is_refused_before_anything_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(user_integrations, "slack_member_id", lambda uid: None)
    sent: list[dict[str, Any]] = []

    # Its own subclass, so a sender going through a list can skip just this
    # person and still let real failures surface.
    with pytest.raises(SlackRecipientNotLinkedError):
        client({"ok": True}, sent).send_dm("user_abc", "x")
    assert sent == []


def test_a_channel_id_is_not_a_dm_recipient() -> None:
    """Review of #478: a C... passed by mistake would post a personal message
    where others read it."""
    sent: list[dict[str, Any]] = []
    with pytest.raises(PermanentIntegrationError, match="not a channel"):
        client({"ok": True}, sent).send_dm("C123", "x")
    assert sent == []


def test_a_slack_member_id_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(uid: str) -> str:
        raise AssertionError("no lookup for a Slack id")

    monkeypatch.setattr(user_integrations, "slack_member_id", never)
    sent: list[dict[str, Any]] = []
    client({"ok": True, "ts": "1"}, sent).send_dm("U999", "x")
    assert sent[0]["channel"] == "U999"


def test_slacks_ok_false_is_a_failure_not_a_delivery() -> None:
    """#280: channel_not_found came back as HTTP 200 and was recorded as sent."""
    with pytest.raises(PermanentIntegrationError, match="channel_not_found"):
        client({"ok": False, "error": "channel_not_found"}, []).post_message("C1", "x")


def test_an_enterprise_grid_member_id_is_passed_through() -> None:
    sent: list[dict[str, Any]] = []
    client({"ok": True, "ts": "1"}, sent).send_dm("W777", "x")
    assert sent[0]["channel"] == "W777"


# --- where a DM landed, and correcting it in place (#586) -------------------------


def test_a_dm_says_which_conversation_and_message_it_became() -> None:
    sent: list[dict[str, Any]] = []

    posted = client(
        {"ok": True, "channel": "D42", "ts": "1726012345.123456"}, sent
    ).send_dm_message("U123", "확인 부탁드립니다")

    assert (posted.channel, posted.ts) == ("D42", "1726012345.123456")


def test_an_update_replaces_the_message_by_its_place() -> None:
    sent: list[dict[str, Any]] = []
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "> [전화번호]로 연락"}}]

    client({"ok": True}, sent).update_message("D42", "1726012345.123456", "확인", blocks)

    assert sent[0]["channel"] == "D42"
    assert sent[0]["ts"] == "1726012345.123456"  # addressing, not read as a bank account
    assert sent[0]["blocks"] == blocks


def test_an_update_is_checked_like_a_send() -> None:
    from autune_core.errors import PrivacyViolationError

    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "> 010-1234-5678로 연락"}}]
    sent: list[dict[str, Any]] = []

    with pytest.raises(PrivacyViolationError):
        client({"ok": True}, sent).update_message("D42", "1.2", "확인", blocks)
    assert sent == []


def test_slacks_refused_update_is_a_failure() -> None:
    with pytest.raises(PermanentIntegrationError, match="message_not_found"):
        client({"ok": False, "error": "message_not_found"}, []).update_message("D42", "1.2", "x")
