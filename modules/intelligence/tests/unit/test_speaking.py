"""Speaking-ratio arithmetic: pure functions over speech segments, no I/O.

The database read and the Slack delivery are the service layer's job; this only
turns a list of (who, how long) into each identified participant's share of the
meeting. See docs/architecture/privacy.md section 3 — the number computed here
is delivered to the speaker and never stored.
"""

from __future__ import annotations

import pytest

from autune_intelligence.speaking import SpeechSegment, speaking_shares


def _seg(participant_id: str | None, start: float, end: float, user_id: str | None = None):
    return SpeechSegment(
        participant_id=participant_id, user_id=user_id, start_sec=start, end_sec=end
    )


def test_two_speakers_split_by_total_duration() -> None:
    shares = speaking_shares([_seg("p_a", 0.0, 30.0, "user_a"), _seg("p_b", 30.0, 40.0, "user_b")])

    by_id = {s.participant_id: s for s in shares}
    assert by_id["p_a"].ratio == pytest.approx(0.75)
    assert by_id["p_b"].ratio == pytest.approx(0.25)
    assert by_id["p_a"].user_id == "user_a"


def test_multiple_segments_for_one_participant_are_summed() -> None:
    shares = speaking_shares(
        [
            _seg("p_a", 0.0, 10.0, "user_a"),
            _seg("p_a", 50.0, 70.0, "user_a"),
            _seg("p_b", 10.0, 40.0, "user_b"),
        ]
    )

    by_id = {s.participant_id: s for s in shares}
    assert by_id["p_a"].seconds == pytest.approx(30.0)
    assert by_id["p_a"].ratio == pytest.approx(0.5)


def test_unattributed_speech_is_excluded_from_the_denominator() -> None:
    shares = speaking_shares([_seg("p_a", 0.0, 30.0, "user_a"), _seg(None, 30.0, 60.0)])

    assert len(shares) == 1
    assert shares[0].participant_id == "p_a"
    assert shares[0].ratio == pytest.approx(1.0)


def test_shares_of_measured_speech_sum_to_one_even_with_unattributed_speech() -> None:
    shares = speaking_shares(
        [
            _seg("p_a", 0.0, 20.0, "user_a"),
            _seg("p_b", 20.0, 30.0, "user_b"),
            _seg(None, 30.0, 90.0),
        ]
    )

    assert sum(s.ratio for s in shares) == pytest.approx(1.0)


def test_a_meeting_with_no_speech_yields_no_shares() -> None:
    assert speaking_shares([]) == []
    assert speaking_shares([_seg("p_a", 10.0, 10.0, "user_a")]) == []


def test_shares_over_identified_participants_do_not_exceed_one() -> None:
    shares = speaking_shares([_seg("p_a", 0.0, 20.0, "user_a"), _seg("p_b", 20.0, 30.0, "user_b")])

    assert sum(s.ratio for s in shares) == pytest.approx(1.0)


def test_one_person_split_across_two_participant_rows_is_counted_once() -> None:
    """Diarization can put one real speaker under two labels (two ``participant``
    rows, same ``user_id`` once identified). If the aggregation key were the
    participant id, this would look like a second speaker — and a co-attendee's
    ``1 - ratio`` would then fix the real person's ratio exactly.
    docs/architecture/privacy.md section 3 forbids that, so the two rows must
    collapse into one share, keyed on the person, not the row.
    """
    shares = speaking_shares(
        [
            _seg("p_a1", 0.0, 20.0, "user_a"),
            _seg("p_a2", 20.0, 40.0, "user_a"),
            _seg("p_b", 40.0, 60.0, "user_b"),
        ]
    )

    assert len(shares) == 2
    by_user = {s.user_id: s for s in shares}
    assert by_user["user_a"].seconds == pytest.approx(40.0)
    assert by_user["user_a"].ratio == pytest.approx(40.0 / 60.0)
