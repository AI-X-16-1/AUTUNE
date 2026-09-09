"""Business logic for module B: Structured Extraction.

Owner: 강민구. See docs/modules/extraction.md and ../../CLAUDE.md.

Reads shared entities from ``autune_core``; writes only ``ext_*`` tables.
Never imports another module.
"""

from __future__ import annotations

from autune_core import get_logger
from autune_integrations import SlackApi, assert_personal_delivery

from .confirmations import ConfirmationResponse, build_confirmation_dm

log = get_logger(__name__)


def send_confirmation_dm(
    slack: SlackApi,
    *,
    speaker_id: str,
    recipient_id: str,
    utterance_id: str,
    quoted_text: str,
) -> str:
    """Ask one speaker whether their own weak assent was a commitment.

    ``speaker_id`` and ``recipient_id`` are both taken rather than one, so the
    guard has two values to compare. Passing the same value twice reads as
    redundant right up until somebody adds a "notify the meeting owner too"
    parameter, and then it is the thing that refuses.

    The utterance quoted here is the recipient's own speech, delivered to nobody
    else. ``assert_personal_delivery`` states that as a precondition instead of
    leaving it to whoever next edits the call site — invariant 11, and
    ``docs/architecture/privacy.md`` section 3.

    Masking is checked by the client, which reads the blocks as well as the
    fallback text. It did not always: this function carried its own
    ``check_outbound`` on the quotation until the shared guard learned to look
    inside a structured payload.
    """
    assert_personal_delivery(subject_id=speaker_id, recipient_id=recipient_id, is_direct=True)

    text, blocks = build_confirmation_dm(utterance_id=utterance_id, quoted_text=quoted_text)
    timestamp = slack.send_dm(recipient_id, text, blocks)

    # Ids only. The utterance is meeting content and a log line is a store.
    log.info("extraction_confirmation_sent", utterance_id=utterance_id)
    return timestamp


def apply_confirmation_response(response: ConfirmationResponse) -> None:
    """Record what the speaker answered.

    The seam the Slack handler delegates to. Persistence lands with
    ``ext_confirmations`` in #12: the table does not exist yet, and inventing it
    here would put a module table outside the migration that owns it.

    What is settled is the shape — one response resolves one utterance to one
    kind — so the handler and the message can be finished and tested now, and
    this function grows a body rather than a caller.
    """
    log.info(
        "extraction_confirmation_received",
        utterance_id=response.utterance_id,
        resolved_kind=response.resolved_kind.value,
    )
    # TODO(강민구): #12 — upsert ext_confirmations, reclassify the utterance in
    # ext_classifications, and build an action item when the answer is a
    # commitment. Idempotent on utterance_id: a second click must not create a
    # second card.
