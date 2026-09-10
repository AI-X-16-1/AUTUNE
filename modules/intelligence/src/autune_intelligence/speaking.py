"""Speaking-ratio arithmetic for module E (pipeline step 7).

Pure functions over values — no database, no Slack, no settings. The service
layer reads utterances and delivers the result; this only turns a list of
(who spoke, for how long) into each identified participant's share of the
meeting.

The number produced here is private to the speaker: it is DM'd to that person
and never persisted, never put in a contract, never returned for anyone else.
See docs/architecture/privacy.md section 3.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechSegment:
    """One utterance reduced to who spoke and for how long.

    ``participant_id`` is ``None`` for speech that was diarized but not matched
    to a participant; it still happened, so it counts toward the meeting total.
    """

    participant_id: str | None
    user_id: str | None
    start_sec: float
    end_sec: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


@dataclass(frozen=True)
class SpeakingShare:
    """One identified participant's share of the meeting's speech time."""

    participant_id: str
    user_id: str | None
    seconds: float
    ratio: float


def speaking_shares(segments: Iterable[SpeechSegment]) -> list[SpeakingShare]:
    """Each identified participant's share of total speech time.

    The denominator is *all* speech, unattributed included, so a participant's
    ratio is their share of the meeting rather than their share of the parts we
    could attribute. Returns an empty list when nobody spoke.
    """
    segs = list(segments)
    total = sum(s.duration for s in segs)
    if total <= 0:
        return []

    seconds: dict[str, float] = {}
    user_ids: dict[str, str | None] = {}
    for seg in segs:
        if seg.participant_id is None:
            continue
        seconds[seg.participant_id] = seconds.get(seg.participant_id, 0.0) + seg.duration
        if seg.user_id is not None:
            user_ids[seg.participant_id] = seg.user_id
        user_ids.setdefault(seg.participant_id, None)

    return [
        SpeakingShare(
            participant_id=pid,
            user_id=user_ids[pid],
            seconds=secs,
            ratio=secs / total,
        )
        for pid, secs in seconds.items()
        if secs > 0
    ]
