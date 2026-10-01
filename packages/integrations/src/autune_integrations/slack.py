"""Slack.

Every module notifies through this client, so message conventions and the
outbound privacy check live in one place.

Message style comes from docs/design/ui-spec.md section 2: status is "●" plus
text rather than an emoji, hierarchy comes from weight, at most three buttons
and only the first is primary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .base import HttpClient
from .errors import PermanentIntegrationError, SlackRecipientNotLinkedError
from .privacy import assert_personal_delivery

AUTUNE_USER_PREFIX = "user_"
SLACK_PERSON_PREFIXES = ("U", "W")  # member ids; W is Enterprise Grid

DESTINATION = "slack"


class SlackApi(Protocol):
    """What modules depend on. `FakeSlack` implements it for tests."""

    def post_message(self, channel: str, text: str, blocks: list[dict] | None = ...) -> str: ...
    def reply_in_thread(self, channel: str, thread_ts: str, text: str) -> str: ...
    def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = ...) -> str: ...
    def send_dm_message(
        self, user_id: str, text: str, blocks: list[dict] | None = ...
    ) -> PostedMessage: ...
    def update_message(
        self, channel: str, ts: str, text: str, blocks: list[dict] | None = ...
    ) -> None: ...


@dataclass(frozen=True)
class PostedMessage:
    """Where a sent message is: the conversation Slack put it in (for a DM, the
    ``D...`` channel Slack opened) and its ``ts``. What ``update_message`` needs to
    change it later (#586)."""

    channel: str
    ts: str


def slack_body(
    channel: str, text: str, blocks: list[dict] | None = None, thread_ts: str | None = None
) -> dict[str, Any]:
    """The body every Slack call sends, real or fake.

    One builder because the fake had its own and they drifted: the fake left
    ``thread_ts`` out, so it checked a body the client does not send and a test
    could pass on a payload production refuses. A second copy of "what we send"
    is a second answer to "what is checked".
    """
    body: dict[str, Any] = {"channel": channel, "text": text}
    if blocks:
        body["blocks"] = blocks
    if thread_ts is not None:
        body["thread_ts"] = thread_ts
    return body


class SlackClient(HttpClient):
    service = "slack"

    addressing = frozenset({"channel", "thread_ts", "ts"})
    """Where the message goes, not what it says.

    `channel` is a channel id, or a user id for a DM. `thread_ts` is Slack's own
    timestamp -- `1726012345.123456` -- which is three groups of digits and is
    therefore a bank account to any pattern that reads it as content; `ts` is the
    same timestamp naming the message ``update_message`` changes. All are
    supplied by the feature and neither came out of a meeting, so checking them
    can only produce false refusals. `text` and `blocks` are still checked.
    """

    def __init__(self, bot_token: str) -> None:
        super().__init__(
            "https://slack.com/api",
            {"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json"},
        )

    def post_message(self, channel: str, text: str, blocks: list[dict] | None = None) -> str:
        return self._post(slack_body(channel, text, blocks))

    def reply_in_thread(self, channel: str, thread_ts: str, text: str) -> str:
        return self._post(slack_body(channel, text, thread_ts=thread_ts))

    def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = None) -> str:
        """A direct message to one person.

        Callers pass an Autune user id (``user_...``) -- every DM in the repo
        does -- and Slack needs a member id (``U...``). The id the person linked
        with "Sign in with Slack" is looked up here, once for every module
        (#255). Someone who has not linked is refused by name rather than sent
        to ``channel_not_found``; a Slack member id is passed through."""
        return self.send_dm_message(user_id, text, blocks).ts

    def send_dm_message(
        self, user_id: str, text: str, blocks: list[dict] | None = None
    ) -> PostedMessage:
        """``send_dm``, saying where the message landed, for a caller that may
        need to correct it later (``update_message``, #586)."""
        answer = self._send(slack_body(_member_id(user_id), text, blocks))
        return PostedMessage(channel=str(answer.get("channel", "")), ts=str(answer.get("ts", "")))

    def update_message(
        self, channel: str, ts: str, text: str, blocks: list[dict] | None = None
    ) -> None:
        """``chat.update``: replace a message this bot sent -- a quotation whose
        line was masked again after a PII report (#586). The same outbound check
        as a new message reads the new text and blocks; ``channel`` and ``ts``
        are addressing. Slack's ``ok: false`` raises, as for a send."""
        body = slack_body(channel, text, blocks)
        body["ts"] = ts
        answer = self.request("POST", "/chat.update", json=body)
        if not answer.get("ok", True):
            raise PermanentIntegrationError(f"slack refused the update: {answer.get('error')}")

    def _post(self, body: dict[str, Any]) -> str:
        return str(self._send(body).get("ts", ""))

    def _send(self, body: dict[str, Any]) -> dict[str, Any]:
        """``chat.postMessage``, whose failures arrive as HTTP 200 with
        ``ok: false``. Those were recorded as sent (#280); now they raise, with
        Slack's error code and no content."""
        answer = self.request("POST", "/chat.postMessage", json=body)
        if not answer.get("ok", True):
            raise PermanentIntegrationError(f"slack refused the message: {answer.get('error')}")
        return dict(answer)

    def send_personal(self, *, subject_id: str, recipient_id: str, text: str) -> str:
        """Deliver data that describes exactly one person.

        Used for the speaking-ratio DM (S23). Refuses any recipient but the
        subject, and refuses a channel outright.
        """
        assert_personal_delivery(subject_id=subject_id, recipient_id=recipient_id, is_direct=True)
        return self.send_dm(recipient_id, text)


def _member_id(recipient: str) -> str:
    """A Slack member id for a DM, never a channel: a ``C...`` passed by
    mistake would post a personal message where others read it."""
    if recipient.startswith(AUTUNE_USER_PREFIX):
        from autune_core.user_integrations import slack_member_id

        member = slack_member_id(recipient)
        if member is None:
            raise SlackRecipientNotLinkedError(
                "this person has not linked a Slack account for direct messages"
            )
        return member
    if recipient.startswith(SLACK_PERSON_PREFIXES):
        return recipient
    raise PermanentIntegrationError("a direct message goes to a person, not a channel")
