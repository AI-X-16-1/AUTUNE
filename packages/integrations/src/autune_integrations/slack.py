"""Slack.

Every module notifies through this client, so message conventions and the
outbound privacy check live in one place.

Message style comes from docs/design/ui-spec.md section 2: status is "●" plus
text rather than an emoji, hierarchy comes from weight, at most three buttons
and only the first is primary.
"""

from __future__ import annotations

from typing import Any, Protocol

from .base import HttpClient
from .errors import PermanentIntegrationError
from .privacy import assert_personal_delivery

AUTUNE_USER_PREFIX = "user_"

DESTINATION = "slack"


class SlackApi(Protocol):
    """What modules depend on. `FakeSlack` implements it for tests."""

    def post_message(self, channel: str, text: str, blocks: list[dict] | None = ...) -> str: ...
    def reply_in_thread(self, channel: str, thread_ts: str, text: str) -> str: ...
    def send_dm(self, user_id: str, text: str, blocks: list[dict] | None = ...) -> str: ...


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

    addressing = frozenset({"channel", "thread_ts"})
    """Where the message goes, not what it says.

    `channel` is a channel id, or a user id for a DM. `thread_ts` is Slack's own
    timestamp -- `1726012345.123456` -- which is three groups of digits and is
    therefore a bank account to any pattern that reads it as content. Both are
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
        return self._post(slack_body(_member_id(user_id), text, blocks))

    def _post(self, body: dict[str, Any]) -> str:
        """``chat.postMessage``, whose failures arrive as HTTP 200 with
        ``ok: false``. Those were recorded as sent (#280); now they raise, with
        Slack's error code and no content."""
        answer = self.request("POST", "/chat.postMessage", json=body)
        if not answer.get("ok", True):
            raise PermanentIntegrationError(f"slack refused the message: {answer.get('error')}")
        return str(answer.get("ts", ""))

    def send_personal(self, *, subject_id: str, recipient_id: str, text: str) -> str:
        """Deliver data that describes exactly one person.

        Used for the speaking-ratio DM (S23). Refuses any recipient but the
        subject, and refuses a channel outright.
        """
        assert_personal_delivery(subject_id=subject_id, recipient_id=recipient_id, is_direct=True)
        return self.send_dm(recipient_id, text)


def _member_id(recipient: str) -> str:
    if not recipient.startswith(AUTUNE_USER_PREFIX):
        return recipient
    from autune_core.user_integrations import slack_member_id

    member = slack_member_id(recipient)
    if member is None:
        raise PermanentIntegrationError(
            "this person has not linked a Slack account for direct messages"
        )
    return member
