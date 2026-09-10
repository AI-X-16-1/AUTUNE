"""The decoder can come apart without raising anything.

Whisper locks onto a sentence and repeats it to the end of the file. The
segments still have timings, the words still have probabilities, and the
contract still validates — so nothing downstream notices that four modules are
about to build on one sentence said forty times.

Every number here comes from the seven runs over the module A evaluation
recording, six healthy and one collapsed.
"""

from __future__ import annotations

import pytest

from autune_audio.quality import (
    MIN_SEGMENTS_TO_JUDGE,
    RepetitionReport,
    TranscriptCollapsedError,
    detect_repetition,
)
from autune_audio.schemas import Segment, Transcription


def transcript(texts: list[str]) -> Transcription:
    return Transcription(
        segments=tuple(
            Segment(start=float(i), end=float(i) + 1, text=text, words=())
            for i, text in enumerate(texts)
        ),
        language="ko",
        language_probability=1.0,
        duration=float(len(texts)),
    )


def varied(n: int) -> list[str]:
    return [f"발화 {i}번입니다" for i in range(n)]


class TestTheRunsThatProducedTheThresholds:
    """Six healthy runs and one collapsed, over the same 13-minute recording.

    Parametrised from the measurements rather than restated as prose, so a
    threshold change has to face all seven at once.
    """

    @pytest.mark.parametrize(
        ("run", "segments", "distinct_ratio", "longest_run"),
        [
            ("baseline", 123, 0.92, 1),
            ("hotwords", 123, 0.89, 1),
            ("withprefix", 102, 0.89, 2),
            ("both", 102, 0.92, 2),
            ("noprefix", 96, 0.92, 1),
            # Worse than using no glossary at all, and still a usable transcript.
            # The bar sits above it on purpose: refusing to publish a mediocre
            # meeting costs a person their meeting.
            ("prompt", 168, 0.89, 8),
        ],
    )
    def test_a_healthy_run_publishes(
        self, run: str, segments: int, distinct_ratio: float, longest_run: int
    ) -> None:
        report = RepetitionReport(
            segments=segments,
            distinct_ratio=distinct_ratio,
            longest_repeat_run=longest_run,
            judged=True,
        )
        assert not report.collapsed, run
        report.raise_if_collapsed()

    def test_the_collapsed_run_does_not(self) -> None:
        """One word changed in the glossary produced this.

        `Next.js` swapped for `betweenness` in the hotwords list took a healthy
        102-segment run to 56 segments repeating one line from 148 seconds to
        the end of an 11-minute recording.
        """
        report = RepetitionReport(
            segments=56, distinct_ratio=0.18, longest_repeat_run=42, judged=True
        )
        assert report.collapsed
        with pytest.raises(TranscriptCollapsedError):
            report.raise_if_collapsed()


class TestDetection:
    def test_a_varied_transcript_is_clean(self) -> None:
        report = detect_repetition(transcript(varied(40)))
        assert report.distinct_ratio == 1.0
        assert report.longest_repeat_run == 1
        assert not report.collapsed

    def test_one_sentence_to_the_end_is_caught(self) -> None:
        report = detect_repetition(transcript(varied(10) + ["같은 문장입니다"] * 40))
        assert report.collapsed

    def test_the_run_is_counted_consecutively_not_in_total(self) -> None:
        """ "네." forty times through a meeting is a meeting, not a collapse."""
        agreeable = [x for i in range(30) for x in (f"발화 {i}번입니다", "네.")]
        report = detect_repetition(transcript(agreeable))
        assert report.longest_repeat_run == 1
        assert not report.collapsed

    def test_whitespace_only_segments_are_not_counted_as_repeats(self) -> None:
        report = detect_repetition(transcript(varied(30) + ["  ", "", "   "]))
        assert report.segments == 30
        assert not report.collapsed


class TestShortTranscripts:
    """Below the floor the ratio is noise, and a wrong refusal costs a meeting."""

    def test_a_short_transcript_is_not_judged(self) -> None:
        report = detect_repetition(transcript(["네.", "네.", "네.", "좋습니다"]))
        assert report.distinct_ratio == 0.5
        assert not report.judged
        assert not report.collapsed
        report.raise_if_collapsed()

    def test_the_floor_is_where_judging_starts(self) -> None:
        just_under = detect_repetition(transcript(["같은 문장"] * (MIN_SEGMENTS_TO_JUDGE - 1)))
        at_the_floor = detect_repetition(transcript(["같은 문장"] * MIN_SEGMENTS_TO_JUDGE))
        assert not just_under.judged
        assert at_the_floor.judged
        assert at_the_floor.collapsed

    def test_an_empty_transcript_is_not_a_collapse(self) -> None:
        """Silence is a real answer; the hallucination check is what covers it."""
        report = detect_repetition(transcript([]))
        assert not report.collapsed


def test_the_repr_does_not_repeat_the_repeated_sentence() -> None:
    """This object exists to be logged, and the sentence is meeting content."""
    text = repr(detect_repetition(transcript(["제 번호는 010-1234-5678입니다"] * 30)))
    assert "010" not in text
    assert "collapsed=True" in text
