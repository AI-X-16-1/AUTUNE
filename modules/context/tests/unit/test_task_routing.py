"""Where each entry task sends a meeting next -- publish, late catch-up, or republish.

``service`` is patched out: these pin the routing on its return values, not the
database work behind them (tests/integration covers that).
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from autune_context import tasks
from autune_context.service import LineageOutcome
from autune_contracts import (
    ExtractionResult,
    PrivacyFlags,
    TranscriptMetadata,
    TranscriptReady,
    TranscriptSource,
    Utterance,
)

MEETING = "mtg_routing"


def _extraction_payload() -> dict:
    return ExtractionResult(meeting_id=MEETING, decisions=[]).model_dump(mode="json")


def _transcript_payload() -> dict:
    return TranscriptReady(
        meeting_id=MEETING,
        utterances=[
            Utterance(
                id="utt_1", speaker="화자", start=0.0, end=1.0, text="검색 논의", confidence=0.9
            )
        ],
        metadata=TranscriptMetadata(
            duration=1.0,
            participants=["화자"],
            source=TranscriptSource.FILE_UPLOAD,
            language="ko",
            privacy=PrivacyFlags(original_audio_deleted=True, pii_masked=True),
        ),
    ).model_dump(mode="json")


@pytest.fixture
def enqueued() -> Iterator[dict[str, MagicMock]]:
    with (
        patch.object(tasks, "publish_if_ready") as publish,
        patch.object(tasks, "republish") as republish,
    ):
        yield {"publish_if_ready": publish, "republish": republish}


@pytest.mark.parametrize(
    ("outcome", "force"), [(LineageOutcome.FIRST, False), (LineageOutcome.LATE, True)]
)
def test_lineage_not_yet_carried_goes_through_publish_if_ready(
    enqueued: dict[str, MagicMock], outcome: LineageOutcome, force: bool
) -> None:
    with patch.object(tasks.service, "build_decision_lineage", return_value=outcome):
        tasks.on_extraction_completed(_extraction_payload())

    enqueued["publish_if_ready"].delay.assert_called_once_with(MEETING, force=force)
    enqueued["republish"].delay.assert_not_called()


def test_a_rebuilt_lineage_is_republished_not_gated(enqueued: dict[str, MagicMock]) -> None:
    """The gap this routing closes: a rerun of B for a meeting that already
    published used to reach ``publish_if_ready(force=False)``, which the
    ``published_at`` guard turned away -- E kept the old ``dec_`` ids."""
    with patch.object(tasks.service, "build_decision_lineage", return_value=LineageOutcome.REBUILT):
        tasks.on_extraction_completed(_extraction_payload())

    enqueued["republish"].delay.assert_called_once_with(MEETING)
    enqueued["publish_if_ready"].delay.assert_not_called()


def test_first_topic_linking_arms_the_publish_and_its_fallback(
    enqueued: dict[str, MagicMock],
) -> None:
    with patch.object(tasks.service, "run_topic_linking", return_value=False):
        tasks.on_transcript_ready(_transcript_payload())

    enqueued["publish_if_ready"].delay.assert_called_once_with(MEETING)
    enqueued["publish_if_ready"].apply_async.assert_called_once()
    enqueued["republish"].delay.assert_not_called()


def test_topic_linking_rerun_after_publish_is_republished(
    enqueued: dict[str, MagicMock],
) -> None:
    with patch.object(tasks.service, "run_topic_linking", return_value=True):
        tasks.on_transcript_ready(_transcript_payload())

    enqueued["republish"].delay.assert_called_once_with(MEETING)
    enqueued["publish_if_ready"].delay.assert_not_called()
    enqueued["publish_if_ready"].apply_async.assert_not_called()


@pytest.mark.parametrize("republished", [True, False])
def test_republish_forces_past_the_guard_and_notifies_nobody(republished: bool) -> None:
    with (
        patch.object(tasks.service, "publish_if_ready", return_value=republished) as publish,
        patch.object(tasks, "notify_context_events") as regular_notify,
        patch.object(tasks, "notify_late_drift") as late_drift,
    ):
        tasks.republish(MEETING)

    publish.assert_called_once_with(MEETING, force=True)
    regular_notify.apply_async.assert_not_called()
    late_drift.apply_async.assert_not_called()
