"""E -> the agent layer: an edited report waits for L2 approval (#674)."""

from __future__ import annotations

from autune_contracts import (
    EVENTS,
    INTELLIGENCE_MEETING_REPORT_CHANGED,
    TERMINAL_EVENTS,
    Payload,
    validate_major_version,
)


def test_the_event_is_declared() -> None:
    assert INTELLIGENCE_MEETING_REPORT_CHANGED == "autune.intelligence.meeting_report_changed"
    assert INTELLIGENCE_MEETING_REPORT_CHANGED in EVENTS


def test_a_process_without_the_agent_layer_may_publish_it_to_nobody() -> None:
    assert INTELLIGENCE_MEETING_REPORT_CHANGED in TERMINAL_EVENTS


def test_the_payload_is_the_meeting_id_only() -> None:
    payload = Payload(meeting_id="mtg_abc")

    validate_major_version(payload)
    assert payload.model_dump() == {
        "contract_version": payload.contract_version,
        "meeting_id": "mtg_abc",
    }
