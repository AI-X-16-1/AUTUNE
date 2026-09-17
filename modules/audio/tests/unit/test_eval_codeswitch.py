"""Code-switching metrics, pinned against HiKE's own reference implementation.

MER and PIER are defined by the HiKE benchmark (ThetaOne-AI/HiKE) and by the
PIER paper it borrows from. Where a case carries exact counts, those counts were
produced by HiKE's ``src/metrics/mer.py`` docstring example or by reading its
``pier`` fork line by line — a number here that disagrees with HiKE means our
score cannot be compared with the paper's table.
"""

from __future__ import annotations

import pytest

from autune_audio.eval.codeswitch import (
    PointOfInterestErrorRate,
    fold_loanwords,
    hike_normalise,
    mixed_error_rate,
    mixed_tokens,
    point_of_interest_error_rate,
)


class TestHikeNormalise:
    """HiKE's ``normalize_text`` produced the references, so the hypothesis has
    to go through the same chain or a perfect transcript scores as wrong."""

    def test_contractions_expand_before_the_apostrophe_is_removed(self) -> None:
        assert hike_normalise("Here's what we can't do, let's see") == (
            "here is what we can not do let us see"
        )

    def test_punctuation_is_deleted_not_turned_into_a_space(self) -> None:
        assert hike_normalise("cross-validation, k-fold") == "crossvalidation kfold"
        assert hike_normalise("9.2%") == "92"

    def test_an_em_dash_between_words_is_a_space_not_a_join(self) -> None:
        """jiwer's SubstituteWords({"—": " "}) runs before punctuation deletion,
        so ``word—word`` keeps two words where ``word-word`` becomes one."""
        assert hike_normalise("api—gateway, api-gateway") == "api gateway apigateway"

    def test_bracketed_non_words_are_dropped(self) -> None:
        assert hike_normalise("[음악] 회의 <unk> 시작") == "회의 시작"

    def test_whitespace_is_collapsed(self) -> None:
        assert hike_normalise("  두\t개의   공백 ") == "두 개의 공백"


class TestMixedTokens:
    def test_hangul_is_split_by_syllable_and_latin_by_word(self) -> None:
        assert mixed_tokens("이번 bug는 session logic") == [
            "이",
            "번",
            "bug",
            "는",
            "session",
            "logic",
        ]

    def test_digits_stay_together_like_a_latin_word(self) -> None:
        assert mixed_tokens("2024년") == ["2024", "년"]


class TestMixedErrorRate:
    def test_an_exact_transcript_scores_zero(self) -> None:
        scored = mixed_error_rate("이번 bug는 session에 문제가", "이번 bug는 session에 문제가")
        assert scored.mer == 0.0
        assert scored.reference_tokens == 9

    def test_hike_reference_example_reproduces_its_published_counts(self) -> None:
        """From HiKE ``src/metrics/mer.py``: 30 Hangul syllables + 4 English words.

        제안한→제한화 (2S), accuracy→"r curacy" (S, I), computational→환비테이션을
        (S, 5I), efficiency→"이펙션 시" (S, 3I): S=5, I=9, D=0, N=34, MER 41.18%.
        """
        reference = (
            "실험 결과 제안한 algorithm 은 기존 방법 대비 accuracy 가 향상되었으며 "
            "computational efficiency 또한 크게 개선되었다"
        )
        hypothesis = (
            "실험 결과 제한화 algorithm 은 기존 방법 대비 r curacy 가 향상되었으며 "
            "환비테이션을 이펙션 시 또한 크게 개선되었다"
        )
        scored = mixed_error_rate(reference, hypothesis)
        assert scored.reference_tokens == 34
        assert (scored.substitutions, scored.deletions, scored.insertions) == (5, 0, 9)
        assert scored.mer == pytest.approx(14 / 34)

    def test_case_and_punctuation_are_normalised_on_both_sides(self) -> None:
        assert mixed_error_rate("이번 bug는 session에", "이번 Bug는, session에.").mer == 0.0

    def test_a_hyphenated_term_matches_the_reference_that_lost_its_hyphen(self) -> None:
        """The reviewer's example: a perfect transcript scored MER 0.154 before."""
        scored = mixed_error_rate(
            "지금 적용한 eventdriven architecture 구조 괜찮은 듯",
            "지금 적용한 event-driven architecture 구조 괜찮은 듯",
        )
        assert scored.mer == 0.0

    def test_a_loanword_in_either_spelling_is_the_same_word(self) -> None:
        loanwords = (("버그", "bug"),)
        scored = mixed_error_rate("이번 bug는 고쳤어", "이번 버그는 고쳤어", loanwords=loanwords)
        assert scored.mer == 0.0

    def test_without_the_loanword_list_the_korean_spelling_is_an_error(self) -> None:
        scored = mixed_error_rate("이번 bug는 고쳤어", "이번 버그는 고쳤어")
        # `bug` (one token) became 버, 그 (two): one substitution and one insertion.
        assert (scored.substitutions, scored.deletions, scored.insertions) == (1, 0, 1)

    def test_an_empty_reference_is_refused_rather_than_divided_by(self) -> None:
        with pytest.raises(ValueError):
            mixed_error_rate("", "무언가")

    def test_the_repr_carries_counts_and_never_the_transcript(self) -> None:
        text = repr(mixed_error_rate("연봉 협상 schedule", "연봉 협상 schedule"))
        assert "연봉" not in text and "schedule" not in text
        assert "N=5" in text


class TestFoldLoanwords:
    def test_the_korean_spelling_becomes_the_english_one(self) -> None:
        assert fold_loanwords("세션 로직에 버그", (("버그", "bug"), ("세션", "session"))) == (
            "session 로직에 bug"
        )

    def test_no_loanwords_leaves_the_text_alone(self) -> None:
        assert fold_loanwords("그대로", ()) == "그대로"


LABELED = (
    "<tag 이번> <tag bug> <tag 는> <tag session> management <tag logic> <tag 에> 문제가 있었어"
)


def scored_against_labeled(
    hypothesis: str, *, loanwords: tuple[tuple[str, str], ...] = ()
) -> PointOfInterestErrorRate:
    return point_of_interest_error_rate(LABELED, hypothesis, loanwords=loanwords)


class TestPointOfInterestErrorRate:
    def test_an_exact_transcript_scores_zero_over_the_tagged_words(self) -> None:
        scored = scored_against_labeled("이번 bug 는 session management logic 에 문제가 있었어")
        assert scored.pier == 0.0
        assert scored.poi_words == 6

    def test_a_particle_glued_to_a_latin_word_is_split_before_scoring(self) -> None:
        """The model writes ``bug는``; the annotators tagged ``bug`` and ``는`` apart."""
        scored = scored_against_labeled("이번 bug는 session management logic에 문제가 있었어")
        assert scored.pier == 0.0

    def test_jamo_count_as_hangul_when_splitting_a_particle_off(self) -> None:
        """HiKE's ``add_space`` uses ``\\p{Script=Hangul}``, which includes ㅋㅋ."""
        scored = point_of_interest_error_rate("<tag ok> <tag ㅋㅋ> 진짜", "okㅋㅋ 진짜")
        assert scored.pier == 0.0

    def test_an_error_away_from_the_switch_does_not_count(self) -> None:
        scored = scored_against_labeled("이번 bug는 session management logic에 문제가 있었다")
        assert scored.pier == 0.0

    def test_a_switch_word_in_the_wrong_spelling_costs_two(self) -> None:
        """``버그는`` is one word where the reference has ``bug`` and ``는``: the
        annotators split the particle from the latin word, so the Korean spelling
        is a substitution and a deletion. This is why the loanword list matters."""
        scored = scored_against_labeled("이번 버그는 session management logic에 문제가 있었어")
        assert scored.pier == pytest.approx(2 / 6)
        assert (scored.substitutions, scored.deletions, scored.insertions) == (1, 1, 0)

    def test_the_loanword_list_makes_the_korean_spelling_correct(self) -> None:
        scored = point_of_interest_error_rate(
            LABELED,
            "이번 버그는 session management logic에 문제가 있었어",
            loanwords=(("버그", "bug"),),
        )
        assert scored.pier == 0.0

    def test_a_dropped_switch_word_is_a_deletion(self) -> None:
        scored = scored_against_labeled("이번 bug는 session management 에 문제가 있었어")
        assert (scored.substitutions, scored.deletions, scored.insertions) == (0, 1, 0)

    def test_a_word_inserted_before_a_switch_word_counts(self) -> None:
        scored = scored_against_labeled("이번 bug는 the session management logic에 문제가 있었어")
        assert (scored.substitutions, scored.deletions, scored.insertions) == (0, 0, 1)

    def test_a_word_inserted_after_the_last_switch_word_does_not_count(self) -> None:
        """As HiKE runs it. Its ``pier_fixed`` appends a dummy token to both sides,
        so a trailing insertion lands before the dummy, which is never tagged."""
        labeled = "그 얘기 들었을 때 나 <tag 진짜> <tag lost> for <tag words> <tag 였어>"
        scored = point_of_interest_error_rate(
            labeled, "그 얘기 들었을 때 나 진짜 lost for words 였어 응"
        )
        assert scored.poi_words == 4
        assert (scored.substitutions, scored.deletions, scored.insertions) == (0, 0, 0)

    def test_a_reference_with_no_tags_is_refused(self) -> None:
        with pytest.raises(ValueError):
            point_of_interest_error_rate("태그가 없는 문장", "태그가 없는 문장")

    def test_the_repr_carries_counts_and_never_the_transcript(self) -> None:
        text = repr(point_of_interest_error_rate("<tag 연봉> <tag schedule>", "연봉 schedule"))
        assert "연봉" not in text and "schedule" not in text
        assert "POI=2" in text
