"""Is this transcript worth publishing, or did the decoder come apart?

Whisper has a failure mode that produces no error: it locks onto a sentence and
repeats it for the rest of the file. Everything downstream still works — the
segments have timings, the words have probabilities, `TranscriptReady` validates
— and B, C and D consume a transcript of one sentence said four hundred times.

It is not rare or exotic. Changing a single term in the glossary caused it on
the evaluation recording: `Next.js` swapped for `betweenness` in the hotwords
list took a healthy 102-segment run to 56 segments repeating one line from 148
seconds to the end. A code review cannot see that, and neither can a test that
does not run a model.

So the pipeline measures its own output. The thresholds below come from the runs
that produced them rather than from taste.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from autune_core import get_logger
from autune_core.errors import AutuneError

from .schemas import Transcription

log = get_logger(__name__)

# Seven runs over the same 13-minute recording, six healthy and one collapsed:
#
#     run           segments   unique/total   longest repeat run
#     baseline           123           0.92                    1
#     hotwords           123           0.89                    1
#     both               102           0.92                    2
#     withprefix         102           0.89                    2
#     noprefix            96           0.92                    1
#     prompt             168           0.89                    8
#     collapsed           56           0.18                   42
#
# The distinct-ratio separates them with nothing in between: every healthy run
# sits at or above 0.89 and the collapsed one at 0.18. Half is a long way from
# either.
MIN_DISTINCT_RATIO = 0.5

# A person does not say the same sentence ten times running. `prompt` reached
# eight and was still a usable transcript — worse than no glossary at all, but
# not garbage — so the bar sits above it. Catching a merely mediocre run here
# would mean refusing to publish a meeting that a reader could use.
MAX_REPEAT_RUN = 10

# Below this the *ratio* is noise: a four-segment meeting where two are "네." is
# 0.5 and perfectly fine. The consecutive-run bar is not gated on it.
MIN_SEGMENTS_TO_JUDGE = 20

# Short segments do not count toward the run. "네." ten times in a row is a
# roll-call or an answer to "들리세요?", not a decoder loop, and refusing that
# meeting costs somebody the meeting. Whisper's own silence hallucination —
# "시청해 주셔서 감사합니다." — is far longer than this and still counts.
MIN_CHARS_TO_COUNT_AS_A_REPEAT = 6


class TranscriptCollapsedError(AutuneError):
    """The decoder repeated itself, twice, and this meeting has no transcript.

    Raised rather than logged: a collapsed transcript reaching the database is
    worse than no transcript, because four modules build on it and nothing
    downstream can tell.

    **This is terminal for the meeting, not a retry.** By the time it is raised
    the recording is gone — ``storage.adopt`` deletes in a ``finally`` and
    invariant 11 does not bend for a failed job — so there is nothing left to
    run again. Recovery happens earlier, inside ``pipeline.transcribe``, while
    the waveform is still in memory: a collapse there is retried once without
    the glossary and without ``condition_on_previous_text``. This exception
    means that retry collapsed too.
    """

    code = "transcript_collapsed"
    status_code = 422


@dataclass(frozen=True)
class RepetitionReport:
    """How much of the transcript is the same sentence over again."""

    segments: int
    distinct_ratio: float
    longest_repeat_run: int
    judged: bool
    """False when the transcript is too short for the ratio to mean anything."""

    @property
    def collapsed(self) -> bool:
        """The ratio needs a long transcript to mean anything. The run does not.

        ``judged`` gates only the ratio. Fifteen identical segments in a row is a
        collapse whether the meeting had eighteen segments or eight hundred —
        unlike the ratio, it does not get noisier as the transcript gets shorter.
        """
        ratio_collapsed = self.judged and self.distinct_ratio < MIN_DISTINCT_RATIO
        return ratio_collapsed or self.longest_repeat_run >= MAX_REPEAT_RUN

    def __repr__(self) -> str:
        """Counts and ratios. The repeated sentence is still meeting content."""
        return (
            f"RepetitionReport(segments={self.segments}, "
            f"distinct={self.distinct_ratio:.2f}, run={self.longest_repeat_run}, "
            f"collapsed={self.collapsed})"
        )

    def raise_if_collapsed(self) -> None:
        """Call before anything writes the transcript down or publishes it."""
        if not self.collapsed:
            return
        raise TranscriptCollapsedError(
            f"the decoder repeated itself: {self.segments} segments, "
            f"{self.distinct_ratio:.2f} distinct, longest run {self.longest_repeat_run}. "
            "The fallback pass collapsed as well, so this meeting has no usable "
            "transcript and the recording is already deleted. Do not re-run the "
            "job — there is nothing to read. See docs/modules/audio.md.",
            segments=self.segments,
        )


def detect_repetition(transcription: Transcription) -> RepetitionReport:
    """Measure repetition in ``transcription``, without reading it aloud.

    Segment text is compared after stripping, and nothing about the text leaves
    this function — the report carries counts only, because it is exactly the
    kind of object that ends up in a log line.
    """
    texts = [segment.text.strip() for segment in transcription.segments if segment.text.strip()]
    if not texts:
        return RepetitionReport(segments=0, distinct_ratio=1.0, longest_repeat_run=0, judged=False)

    # Counted over the substantial segments only; see the constant. Two loops
    # this does not catch: a decoder alternating between two sentences has a run
    # of one, and if the meeting was long enough its ratio stays above the bar.
    # It is a rarer shape than a single repeated line and is left visible here
    # rather than guessed at.
    substantial = [t for t in texts if len(t) >= MIN_CHARS_TO_COUNT_AS_A_REPEAT]
    longest = current = 1 if substantial else 0
    for previous, this in zip(substantial, substantial[1:], strict=False):
        current = current + 1 if this == previous else 1
        longest = max(longest, current)

    return RepetitionReport(
        segments=len(texts),
        distinct_ratio=len(Counter(texts)) / len(texts),
        longest_repeat_run=longest,
        judged=len(texts) >= MIN_SEGMENTS_TO_JUDGE,
    )
