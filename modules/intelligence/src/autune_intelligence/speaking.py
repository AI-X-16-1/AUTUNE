"""Speaking-ratio arithmetic for module E (pipeline step 7).

Pure functions over values — no database, no Slack, no settings. The service
layer reads utterances and delivers the result; this only turns a list of
(who spoke, for how long) into each identified participant's share of the
speech we could attribute.

The denominator is attributed speech only. Speech that was diarized but never
matched to a participant is dropped from both numerator and denominator, so the
shares of the measured participants sum to 1.0 and the even-share baseline the
DM shows (``100 / participant_count``) is a mean they can actually sit above or
below. This is a share of *measured* speech, not of the whole meeting.

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
    to a participant. It is not counted — the ratio is a share of attributed
    speech, so unmatched speech would only depress every participant's number
    below a baseline none of them could reach.
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
    """Each identified participant's share of *attributed* speech time.

    The denominator is speech matched to a participant; segments with no
    ``participant_id`` are ignored entirely. A participant's ratio is therefore
    their share of the speech we could measure, and the ratios sum to 1.0.
    Returns an empty list when no attributed speech was found.
    """
    segs = list(segments)
    total = sum(s.duration for s in segs if s.participant_id is not None)
    if total <= 0:
        return []

    seconds: dict[str, float] = {}
    user_ids: dict[str, str | None] = {}
    for seg in segs:
        pid = seg.participant_id
        if pid is None:
            continue
        seconds[pid] = seconds.get(pid, 0.0) + seg.duration
        if seg.user_id is not None:
            user_ids[pid] = seg.user_id
        user_ids.setdefault(pid, None)

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
