"""Korean scoring, pinned against the module A evaluation recording.

Every number asserted here was produced by the real recording described in the
cue sheet, not invented for the test. Where a case looks arbitrary, it is a
failure the recording actually contained.
"""

from __future__ import annotations

import pytest

from autune_audio.eval.korean import (
    character_error_rate,
    hallucinated_characters,
    normalise,
    term_accuracy,
)


class TestCharacterErrorRate:
    def test_an_exact_transcript_scores_zero(self) -> None:
        assert character_error_rate("회의 시작하겠습니다", "회의 시작하겠습니다").cer == 0.0

    def test_it_is_the_reason_this_module_does_not_report_wer(self) -> None:
        """One wrong syllable fails a whole 어절, so WER reads as total failure."""
        scored = character_error_rate("진행하겠습니다", "진행하겠읍니다")
        assert scored.cer == pytest.approx(1 / 7)
        assert (scored.substitutions, scored.deletions, scored.insertions) == (1, 0, 0)

    def test_the_three_error_kinds_are_counted_apart(self) -> None:
        substituted = character_error_rate("가나다", "가라다")
        assert (substituted.substitutions, substituted.deletions, substituted.insertions) == (
            1,
            0,
            0,
        )

        deleted = character_error_rate("가나다", "가다")
        assert (deleted.substitutions, deleted.deletions, deleted.insertions) == (0, 1, 0)

        inserted = character_error_rate("가다", "가나다")
        assert (inserted.substitutions, inserted.deletions, inserted.insertions) == (0, 0, 1)

    def test_spacing_is_not_an_error(self) -> None:
        """Korean spacing varies between speakers and between models."""
        assert character_error_rate("6개월 차 목표는", "6개월차 목표는").cer == 0.0

    def test_an_empty_reference_is_refused_rather_than_divided_by(self) -> None:
        with pytest.raises(ValueError):
            character_error_rate("", "무언가")

    def test_the_repr_carries_counts_and_never_the_transcript(self) -> None:
        text = repr(character_error_rate("사내 연봉 협상 회의", "사내 연봉 협상 회의"))
        assert "연봉" not in text
        assert "N=8" in text


class TestNormalise:
    def test_spoken_numerals_become_digits_a_date_parser_can_read(self) -> None:
        assert normalise("구월 십팔일") == "9월 18일"

    def test_a_scale_character_is_kept_and_the_numeral_before_it_converted(self) -> None:
        """백팔십억 becomes 180억, not 18000000000.

        Expanding it would make one misheard figure cost eleven characters of
        CER instead of four, which is not what a listener would notice.
        """
        assert normalise("백팔십억 달러") == "180억 달러"
        assert normalise("백팔십억 달러") == normalise("180억 달러")
        assert normalise("천오백만 달러") == normalise("1,500만 달러")
        assert normalise("이천사백칠십오달러") == normalise("2,475달러")

    def test_percentages_and_dollars_are_units_too(self) -> None:
        """S3 is the numbers session; spelling must not show up in its CER."""
        assert normalise("삼십 퍼센트 증가") == normalise("30% 증가")
        assert normalise("백사십사달러") == normalise("144달러")

    def test_a_word_that_merely_starts_with_a_numeral_is_left_alone(self) -> None:
        """원 is not a unit here: it would turn 공원 into 0원 and 사원 into 4원."""
        for word in ("공원에서", "사원 다섯", "제일 먼저", "내일", "병원"):
            assert normalise(word) == word

    def test_composition_across_a_scale_is_a_known_limit(self) -> None:
        """1만 4,400 and 14400 are the same amount, and this does not know it.

        Handling it needs a parser rather than a substitution. Pinned so the
        limit is visible: here the normalised score is worse than the raw one,
        which is the symptom the module docstring says to look for.
        """
        assert normalise("1만 4,400달러") != normalise("14400달러")

    def test_spoken_units_become_the_latin_spelling_the_model_writes(self) -> None:
        """Both spellings are correct; scoring them apart measures orthography."""
        assert normalise("약 30센티미터이며") == normalise("약 30cm이며")
        assert normalise("16킬로헤르츠 모노") == normalise("16kHz 모노")

    def test_a_listed_filler_is_dropped_from_both_sides(self) -> None:
        assert normalise("음 그러니까 그게 아니라") == "그게 아니라"

    def test_an_unlisted_filler_survives_and_that_is_visible_in_the_score(self) -> None:
        """The recording said 뭐였더라; an earlier list had only 뭐지.

        A filler missing from the list is removed from the reference and kept in
        the hypothesis, so the normalised score comes out worse than the raw one.
        Adding the observed form is the fix; the asymmetry is the symptom to
        recognise.
        """
        assert "뭐였더라" not in normalise("그러니까 뭐였더라 그게 아니라")

    def test_punctuation_and_case_do_not_separate_two_equal_transcripts(self) -> None:
        assert normalise("FastAPI, 엔드포인트로!") == normalise("fastapi 엔드포인트로")


class TestHallucination:
    def test_silence_transcribed_as_nothing_is_the_passing_case(self) -> None:
        scored = hallucinated_characters("   ")
        assert (scored.characters, scored.text_appeared) == (0, False)

    def test_subtitle_boilerplate_over_silence_is_counted_by_character(self) -> None:
        scored = hallucinated_characters("시청해주셔서 감사합니다.")
        assert scored.characters == 11
        assert scored.text_appeared is True

    def test_the_repr_never_repeats_the_invented_text(self) -> None:
        """In a log it would read exactly like a real transcript."""
        assert "시청" not in repr(hallucinated_characters("시청해주셔서 감사합니다"))


class TestTermAccuracy:
    def test_a_term_the_model_transliterated_counts_as_missed(self) -> None:
        """pyannote heard as 파이어노트 — the failure the cue sheet predicted."""
        scored = term_accuracy("그 파이어노트 파이프라인을", ["pyannote"])
        assert scored.accuracy == 0.0
        assert scored.missed == ("pyannote",)

    def test_case_and_spacing_do_not_decide_a_term(self) -> None:
        assert term_accuracy("프론트엔드는 next js에", ["Next.js"]).accuracy == 1.0

    def test_spelling_does_decide_a_term(self) -> None:
        assert term_accuracy("데벨타 v3를 파인튜닝해서", ["DeBERTa"]).accuracy == 0.0

    def test_missed_terms_are_named_because_they_are_our_vocabulary(self) -> None:
        """Meeting content stays out of logs; our own stack names may go in."""
        assert "CTranslate2" in repr(term_accuracy("시트랜슬레이트 투", ["CTranslate2"]))

    def test_no_terms_is_refused_rather_than_scored_as_perfect(self) -> None:
        with pytest.raises(ValueError):
            term_accuracy("무언가", [])
