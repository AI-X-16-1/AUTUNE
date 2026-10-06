"""E -> C: where a meeting's report went out, so C can reply in its thread (#824)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autune_contracts import (
    EVENTS,
    INTELLIGENCE_MEETING_REPORT_POSTED,
    TERMINAL_EVENTS,
    MeetingReportPosted,
    validate_major_version,
)


def test_the_event_is_declared() -> None:
    assert INTELLIGENCE_MEETING_REPORT_POSTED == "autune.intelligence.meeting_report_posted"
    assert INTELLIGENCE_MEETING_REPORT_POSTED in EVENTS


def test_it_may_reach_no_task_until_c_subscribes() -> None:
    assert INTELLIGENCE_MEETING_REPORT_POSTED in TERMINAL_EVENTS


def test_the_payload_is_the_meeting_the_channel_and_the_thread() -> None:
    payload = MeetingReportPosted(
        meeting_id="mtg_abc", channel="C0123", thread_ts="1728200000.000100"
    )

    validate_major_version(payload)
    assert payload.model_dump() == {
        "contract_version": payload.contract_version,
        "meeting_id": "mtg_abc",
        "channel": "C0123",
        "thread_ts": "1728200000.000100",
    }


@pytest.mark.parametrize("missing", ["channel", "thread_ts"])
def test_a_post_without_its_place_is_refused(missing: str) -> None:
    fields = {"meeting_id": "mtg_abc", "channel": "C0123", "thread_ts": "1.2"}
    fields[missing] = ""

    with pytest.raises(ValidationError):
        MeetingReportPosted(**fields)
