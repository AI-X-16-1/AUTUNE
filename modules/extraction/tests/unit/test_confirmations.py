"""The ambiguous-agreement confirmation DM: shape, routing, and who may see it.

Everything here runs against ``FakeSlack``, which records instead of sending and
runs the same outbound guards as the real client. No credentials, and the Slack
app does not exist yet (#1).
"""

from __future__ import annotations

import pytest

from autune_contracts.enums import UtteranceKind
from autune_core.errors import PrivacyViolationError
from autune_extraction import confirmations, slack
from autune_extraction.confirmations import (
    CONFIRM_COMMITMENT,
    CONFIRM_DECISION,
    DENY,
    ConfirmationError,
    build_confirmation_dm,
    parse_confirmation_action,
)
from autune_extraction.service import send_confirmation_dm
from autune_integrations.fakes import FakeSlack

UTTERANCE = "utt_9f2"
QUOTED = "한번 볼게요"


def _payload(action_id: str, *, value: str = UTTERANCE, user: str = "U_SPEAKER") -> dict:
    return {"user": {"id": user}, "actions": [{"action_id": action_id, "value": value}]}


# --- what the message looks like -------------------------------------------


def test_the_message_follows_the_slackblocks_rules() -> None:
    """ui-spec section 2: "●" not an emoji, at most 3 buttons, only the first primary."""
    _, blocks = build_confirmation_dm(utterance_id=UTTERANCE, quoted_text=QUOTED)
    buttons = [b for block in blocks if block["type"] == "actions" for b in block["elements"]]

    assert len(buttons) <= 3
    assert buttons[0].get("style") == "primary"
    assert all("style" not in b for b in buttons[1:])
    assert any("●" in str(block) for block in blocks)


def test_every_button_carries_the_utterance_it_answers() -> None:
    """Without it the response has nothing to resolve."""
    _, blocks = build_confirmation_dm(utterance_id=UTTERANCE, quoted_text=QUOTED)
    buttons = [b for block in blocks if block["type"] == "actions" for b in block["elements"]]

    assert {b["value"] for b in buttons} == {UTTERANCE}


def test_the_notification_fallback_does_not_quote_the_meeting() -> None:
    """The fallback is what a phone shows on a lock screen, to whoever holds it."""
    text, blocks = build_confirmation_dm(utterance_id=UTTERANCE, quoted_text=QUOTED)

    assert QUOTED not in text
    assert any(QUOTED in str(block) for block in blocks)


# --- what comes back --------------------------------------------------------


@pytest.mark.parametrize(
    ("action_id", "expected"),
    [
        (CONFIRM_COMMITMENT, UtteranceKind.COMMITMENT),
        (CONFIRM_DECISION, UtteranceKind.DECISION),
        (DENY, UtteranceKind.CONCERN),
    ],
)
def test_each_button_resolves_to_its_kind(action_id: str, expected: UtteranceKind) -> None:
    """ui-spec: promoted to commitment or decision when confirmed, demoted when denied."""
    response = parse_confirmation_action(_payload(action_id))

    assert response.resolved_kind is expected
    assert response.utterance_id == UTTERANCE
    assert response.responder_id == "U_SPEAKER"


def test_denial_becomes_a_concern_rather_than_disappearing() -> None:
    """A speaker declining to commit is still something the meeting said, and C reads it."""
    assert parse_confirmation_action(_payload(DENY)).resolved_kind is UtteranceKind.CONCERN


def test_only_a_commitment_answer_reads_as_one() -> None:
    assert parse_confirmation_action(_payload(CONFIRM_COMMITMENT)).is_commitment
    assert not parse_confirmation_action(_payload(CONFIRM_DECISION)).is_commitment


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"user": {"id": "U1"}, "actions": []},
        {"user": {"id": "U1"}, "actions": [{"action_id": "someone_elses", "value": UTTERANCE}]},
        {"user": {"id": "U1"}, "actions": [{"action_id": CONFIRM_COMMITMENT, "value": "act_1"}]},
        {"actions": [{"action_id": CONFIRM_COMMITMENT, "value": UTTERANCE}]},
    ],
    ids=["empty", "no-actions", "foreign-action", "not-an-utterance-id", "no-user"],
)
def test_a_payload_we_do_not_model_raises_rather_than_guessing(payload: dict) -> None:
    with pytest.raises(ConfirmationError):
        parse_confirmation_action(payload)


def test_the_parse_error_never_carries_the_slack_payload() -> None:
    """A Slack action body holds the message text, and an exception reaches error tracking."""
    secret = "박재경 님 010-1234-5678 로 연락"
    payload = {
        "user": {"id": "U1"},
        "message": {"text": secret},
        "actions": [{"action_id": "someone_elses", "value": UTTERANCE}],
    }

    with pytest.raises(ConfirmationError) as caught:
        parse_confirmation_action(payload)

    assert secret not in str(caught.value)
    assert "010-1234" not in str(caught.value)


# --- who may see it ---------------------------------------------------------


def test_the_question_goes_to_the_speaker_by_direct_message() -> None:
    fake = FakeSlack()

    send_confirmation_dm(
        fake,
        speaker_id="U_SPEAKER",
        recipient_id="U_SPEAKER",
        utterance_id=UTTERANCE,
        quoted_text=QUOTED,
    )

    assert len(fake.sent) == 1
    assert fake.sent[0].is_dm
    assert fake.sent[0].channel == "U_SPEAKER"
    assert fake.channel_messages == []


def test_sending_one_speakers_utterance_to_anyone_else_is_refused() -> None:
    """Invariant 11. The guard is a precondition, not a convention at the call site."""
    fake = FakeSlack()

    with pytest.raises(PrivacyViolationError):
        send_confirmation_dm(
            fake,
            speaker_id="U_SPEAKER",
            recipient_id="U_MANAGER",
            utterance_id=UTTERANCE,
            quoted_text=QUOTED,
        )

    assert fake.sent == []


def test_unmasked_text_is_refused_before_it_reaches_slack() -> None:
    """Masking happens before the write, so this should be unreachable — checked anyway.

    The client's own guard cannot catch this one. ``check_outbound`` reads the
    fallback text, and every message this module sends carries its content in
    blocks, which nothing inspects. So the check is made here, in module B, until
    ``packages/integrations`` closes it — see the comment at the call site.

    Without that line this test fails by sending, which is how the gap was found.
    """
    fake = FakeSlack()

    with pytest.raises(PrivacyViolationError):
        send_confirmation_dm(
            fake,
            speaker_id="U_SPEAKER",
            recipient_id="U_SPEAKER",
            utterance_id=UTTERANCE,
            quoted_text="제 번호는 010-1234-5678 입니다",
        )

    assert fake.sent == []


def test_the_clients_own_guard_now_sees_blocks() -> None:
    """``packages/integrations`` inspects blocks, so this module's own check is
    redundant — kept as the record that the gap is closed.

    It used to pin the opposite: the guard read only the fallback text, so an
    unmasked quotation inside blocks sent cleanly. Closing that in
    ``check_outbound`` is what flipped this test.
    """
    fake = FakeSlack()
    _, blocks = build_confirmation_dm(
        utterance_id=UTTERANCE, quoted_text="제 번호는 010-1234-5678 입니다"
    )

    with pytest.raises(PrivacyViolationError):
        fake.send_dm("U_SPEAKER", "확인이 필요합니다.", blocks)

    assert fake.sent == []


def test_every_button_has_a_handler_registered() -> None:
    """A button with no handler leaves the speaker's click doing nothing, silently."""
    registered: list[str] = []

    class RecordingApp:
        def action(self, action_id: str):  # noqa: ANN202
            registered.append(action_id)
            return lambda fn: fn

    slack.register(RecordingApp())

    _, blocks = build_confirmation_dm(utterance_id=UTTERANCE, quoted_text=QUOTED)
    buttons = [b for block in blocks if block["type"] == "actions" for b in block["elements"]]

    assert set(registered) == {b["action_id"] for b in buttons}
    assert set(registered) == set(confirmations.ACTION_IDS)
