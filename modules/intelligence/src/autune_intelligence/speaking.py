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
    """Each identified person's share of *attributed* speech time.

    The denominator is speech matched to a participant; segments with no
    ``participant_id`` are ignored entirely. Grouped by ``user_id`` where known
    rather than ``participant_id``: diarization can split one real speaker into
    two participant rows (two labels later confirmed as the same person), and
    counting that as two people would let a co-attendee derive the real
    person's exact ratio from ``1 - their own`` — see the ``_MIN_SPEAKERS_FOR_RATIO``
    docstring in ``service.py``. A person's ratio is their share of the speech we
    could measure, and the ratios sum to 1.0. Returns an empty list when no
    attributed speech was found.
    """
    segs = list(segments)
    total = sum(s.duration for s in segs if s.participant_id is not None)
    if total <= 0:
        return []

    seconds: dict[str, float] = {}
    participant_ids: dict[str, str] = {}
    user_ids: dict[str, str | None] = {}
    for seg in segs:
        pid = seg.participant_id
        if pid is None:
            continue
        key = seg.user_id if seg.user_id is not None else pid
        seconds[key] = seconds.get(key, 0.0) + seg.duration
        participant_ids.setdefault(key, pid)
        user_ids[key] = seg.user_id

    return [
        SpeakingShare(
            participant_id=participant_ids[key],
            user_id=user_ids[key],
            seconds=secs,
            ratio=secs / total,
        )
        for key, secs in seconds.items()
        if secs > 0
    ]


def speaker_count_for_gate(shares: Iterable[SpeakingShare]) -> int:
    """How many distinct *identified* people this meeting counts as, for gating.

    An unidentified share (``user_id is None``) contributes nothing. It is
    tempting to count all unidentified shares together as "at most one more
    person" — but that undercounts the wrong thing: an unidentified label
    might be a new person, or it might be the not-yet-confirmed other half of
    a speaker who *is* already identified. Those two cases are
    indistinguishable before identification finishes, and the second one means
    "identified count + 1" can overstate the real population by exactly one —
    which is the whole population the ``_MIN_SPEAKERS_FOR_RATIO`` guard exists
    to protect at N=2. Only counting shares already resolved to a person is
    the version that cannot be wrong in that direction; the cost is a real
    3-person meeting with one still-unidentified speaker reads as too small
    until identification catches up (idempotent: the caller recomputes this on
    every request, so it self-corrects — see ``docs/modules/intelligence.md``
    step 7).
    """
    return sum(1 for s in shares if s.user_id is not None)
