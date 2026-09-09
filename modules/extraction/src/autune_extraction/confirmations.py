"""The ambiguous-agreement confirmation DM: what it says and what comes back.

Pure functions over values. No Slack client, no database, no settings — the
service layer sends what this builds, and the handler in ``slack.py`` feeds it
what Slack sends back. Keeping it separate is what lets the message shape and
the button routing be tested without credentials, which do not exist until the
workspace app is created (#1).

Weak assent is the case this exists for. "한번 볼게요" is not a commitment and
must not become an action item on its own; the speaker is asked, and their
answer decides. The question goes to the speaker by direct message and never to
a channel — ``/CLAUDE.md`` invariant 11, and the guard is in ``service.py``
where the send happens.

Message shape follows ``docs/design/ui-spec.md`` section 2, SlackBlocks: status
is "●" plus text rather than an emoji, hierarchy comes from weight, at most
three buttons and only the first is primary.

The three outcomes come from the same document's state mapping: an ambiguous
utterance is *promoted to commitment or decision when confirmed, demoted to
concern when denied*.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autune_contracts.enums import UtteranceKind

CONFIRM_COMMITMENT = "autune_extraction.confirm_commitment"
CONFIRM_DECISION = "autune_extraction.confirm_decision"
DENY = "autune_extraction.deny"

ACTION_IDS: dict[str, UtteranceKind] = {
    CONFIRM_COMMITMENT: UtteranceKind.COMMITMENT,
    CONFIRM_DECISION: UtteranceKind.DECISION,
    DENY: UtteranceKind.CONCERN,
}
"""Which button resolves the utterance to which kind.

`DENY` lands on ``concern`` rather than dropping the utterance: the speaker
declining to commit is itself something the meeting said, and module C reads
concerns. Silence is not in this table — a DM nobody answers has no button
click, and the 24-hour timeout (#12) resolves it as undecided.
"""


class ConfirmationError(ValueError):
    """A Slack payload that is not a confirmation response.

    Never carries the payload. A Slack action body holds the message text, which
    is meeting content, and an exception message reaches error tracking.
    """


@dataclass(frozen=True)
class ConfirmationResponse:
    utterance_id: str
    resolved_kind: UtteranceKind
    responder_id: str

    @property
    def is_commitment(self) -> bool:
        return self.resolved_kind is UtteranceKind.COMMITMENT


def build_confirmation_dm(*, utterance_id: str, quoted_text: str) -> tuple[str, list[dict]]:
    """The DM asking one speaker to resolve one ambiguous agreement.

    ``quoted_text`` is the utterance as stored, which is already PII-masked —
    transcript text is masked before it is written (invariant 11), so what comes
    out of the database is what may be sent. The outbound guard in the client
    checks it again rather than trusting that.

    Returns the fallback text and the blocks. The fallback carries no quotation:
    it is what Slack shows in a notification preview and on the lock screen of a
    phone the speaker may not be holding.
    """
    fallback = "회의에서 하신 말씀이 약속이었는지 확인해 주세요."
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "● *확인이 필요합니다*"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"> {quoted_text}"},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "이 발화를 약속으로 볼지 판단이 서지 않아 여쭙습니다. "
                    "답하지 않으시면 미결정으로 남고 액션 아이템이 만들어지지 않습니다.",
                }
            ],
        },
        {
            "type": "actions",
            "elements": [
                _button("약속입니다", CONFIRM_COMMITMENT, utterance_id, primary=True),
                _button("결정입니다", CONFIRM_DECISION, utterance_id),
                _button("아닙니다", DENY, utterance_id),
            ],
        },
    ]
    return fallback, blocks


def _button(label: str, action_id: str, utterance_id: str, *, primary: bool = False) -> dict:
    button: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": label},
        "action_id": action_id,
        "value": utterance_id,
    }
    if primary:
        button["style"] = "primary"
    return button


def parse_confirmation_action(payload: dict) -> ConfirmationResponse:
    """Read one button click into a response.

    Raises ``ConfirmationError`` on anything else. The handler is registered per
    action id, so a mismatch here means Slack sent a shape we do not model —
    worth failing on rather than guessing at.
    """
    actions = payload.get("actions") or []
    if not actions:
        raise ConfirmationError("payload carries no actions")

    action = actions[0]
    action_id = action.get("action_id", "")
    if action_id not in ACTION_IDS:
        raise ConfirmationError(f"unknown action_id: {action_id}")

    utterance_id = action.get("value") or ""
    if not utterance_id.startswith("utt_"):
        raise ConfirmationError("action value is not an utterance id")

    responder_id = (payload.get("user") or {}).get("id") or ""
    if not responder_id:
        raise ConfirmationError("payload carries no responding user")

    return ConfirmationResponse(
        utterance_id=utterance_id,
        resolved_kind=ACTION_IDS[action_id],
        responder_id=responder_id,
    )
