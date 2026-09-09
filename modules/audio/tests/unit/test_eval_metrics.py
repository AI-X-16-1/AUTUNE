"""The arithmetic behind module A's three targets.

Written before the corpus arrived, which is the point: the harness scores
structures, so it is testable without audio, without a model and without labels.
"""

from __future__ import annotations

import pytest

from autune_audio.eval.metrics import (
    Turn,
    diarization_error_rate,
    masking_recall,
    word_error_rate,
)


class TestWordErrorRate:
    def test_an_exact_transcript_scores_zero(self) -> None:
        assert (
            word_error_rate("검색 개인화 진행하겠습니다", "검색 개인화 진행하겠습니다").wer == 0.0
        )

    def test_the_three_error_kinds_are_counted_apart(self) -> None:
        """Deletions point at segmentation, substitutions at the acoustic model."""
        substituted = word_error_rate("가 나 다", "가 라 다")
        assert (substituted.substitutions, substituted.deletions, substituted.insertions) == (
            1,
            0,
            0,
        )

        deleted = word_error_rate("가 나 다", "가 다")
        assert (deleted.substitutions, deleted.deletions, deleted.insertions) == (0, 1, 0)

        inserted = word_error_rate("가 다", "가 나 다")
        assert (inserted.substitutions, inserted.deletions, inserted.insertions) == (0, 0, 1)

    def test_the_rate_is_over_reference_length(self) -> None:
        assert word_error_rate("가 나 다 라", "가 나 다 마").wer == 0.25

    def test_a_wholly_wrong_transcript_can_exceed_one(self) -> None:
        """WER is not a percentage: enough insertions push it past 1.0."""
        assert word_error_rate("가", "나 다 라 마").wer > 1.0

    def test_an_empty_reference_raises_rather_than_dividing(self) -> None:
        with pytest.raises(ValueError, match="undefined"):
            word_error_rate("", "무언가")

    def test_the_repr_carries_no_transcript(self) -> None:
        """A metric object may reach a log line; a transcript is meeting content."""
        result = word_error_rate("연락처는 010-1234-5678", "연락처는 010-9999-8888")
        assert "010" not in repr(result)


class TestDiarizationErrorRate:
    def test_a_perfect_split_scores_zero(self) -> None:
        turns = [Turn(0.0, 5.0, "A"), Turn(5.0, 10.0, "B")]
        assert diarization_error_rate(turns, turns) == 0.0

    def test_speaker_names_do_not_matter(self) -> None:
        """pyannote returns SPEAKER_00, not a person. DER scores the split.

        This is why the metric maps labels optimally instead of comparing them,
        and why a hand-rolled implementation gets it wrong.
        """
        reference = [Turn(0.0, 5.0, "김서연"), Turn(5.0, 10.0, "이개발")]
        ours = [Turn(0.0, 5.0, "SPEAKER_00"), Turn(5.0, 10.0, "SPEAKER_01")]
        assert diarization_error_rate(reference, ours) == 0.0

    def test_merging_two_speakers_into_one_is_penalised(self) -> None:
        reference = [Turn(0.0, 5.0, "A"), Turn(5.0, 10.0, "B")]
        merged = [Turn(0.0, 10.0, "A")]
        assert diarization_error_rate(reference, merged) > 0.4

    def test_missing_half_the_speech_is_penalised(self) -> None:
        reference = [Turn(0.0, 5.0, "A"), Turn(5.0, 10.0, "B")]
        assert diarization_error_rate(reference, [Turn(0.0, 5.0, "A")]) > 0.4

    def test_an_empty_reference_raises(self) -> None:
        with pytest.raises(ValueError, match="undefined"):
            diarization_error_rate([], [Turn(0.0, 1.0, "A")])


class TestMaskingRecall:
    def test_masking_everything_the_corpus_masked_scores_one(self) -> None:
        reference = "연락처 010-****-5678 이고 메일은 k***@example.com 입니다"
        assert masking_recall(reference, reference).recall == 1.0

    def test_a_missed_span_lowers_recall(self) -> None:
        """The number that matters: what leaked, not what was over-masked."""
        reference = "연락처 010-****-5678 이고 메일은 k***@example.com 입니다"
        ours = "연락처 010-****-5678 이고 메일은 hong@example.com 입니다"
        result = masking_recall(reference, ours)
        assert result.recall == 0.5
        assert (result.spans_in_reference, result.spans_we_masked) == (2, 1)

    def test_over_masking_does_not_score_above_one(self) -> None:
        """Recall is what we promise. Precision is a separate, smaller worry."""
        reference = "연락처 010-****-5678 입니다"
        ours = "**** 010-****-5678 ****"
        assert masking_recall(reference, ours).recall == 1.0

    def test_a_reference_with_nothing_to_mask_raises(self) -> None:
        with pytest.raises(ValueError, match="undefined"):
            masking_recall("아무것도 없습니다", "아무것도 없습니다")
