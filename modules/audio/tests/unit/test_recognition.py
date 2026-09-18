"""The second detector, and the sentences it must leave alone.

Two directions, and the second one is the reason this file is long. Finding a
phone number somebody read aloud is easy; not turning `일이 있어서` into a
number is the part that decides whether anyone can read the transcript
afterwards.
"""

from __future__ import annotations

import pytest

from autune_audio.masking import mask
from autune_audio.recognition import (
    _DIGIT_SYLLABLES,
    MIN_RUN_DIGITS,
    MIN_SPOKEN_SYLLABLES,
    FakeRecogniser,
    SpokenNumberRecogniser,
    _spell_out,
    get_recogniser,
)
from autune_integrations.privacy import find_unmasked


@pytest.fixture
def recogniser() -> SpokenNumberRecogniser:
    return SpokenNumberRecogniser()


# --- what it is for -------------------------------------------------------

# The two rows issue #143 collected, and the particle variants that decide
# whether the number is read one digit too long.
# (line, the masked result in full, the category counted)
#
# **The whole string, not a substring.** This table checked that the spoken part
# had gone and counted the spans, and that passed while every digit of a mixed
# number stayed: `010-1234 오육칠팔` came out `010-1234 ****`, which is the
# phone number with its last four written in Korean. A test that checks half of
# an output is a test that can only find half of a leak.
SPOKEN = [
    ("제 번호는 공일공 일이삼사 오육칠팔이에요", "제 번호는 *** **** ****이에요", "phone"),
    ("공일공 일이삼사 오육칠팔이랑", "*** **** ****이랑", "phone"),
    ("번호 공일공 일이삼사 오육칠팔입니다", "번호 *** **** ****입니다", "phone"),
    ("공일공 일이삼사 오육칠팔로 연락주세요", "*** **** ****로 연락주세요", "phone"),
    ("공일공일이삼사오육칠팔", "***********", "phone"),
    ("계좌는 국민 삼일이 이사 오육칠팔구공이요", "계좌는 국민 *** ** ******이요", "account"),
    ("주민번호 구공공일공일 일이삼사오육칠이에요", "주민번호 ****** ********에요", "rrn"),
    # Written in both scripts. Whisper does not pick one for a whole number.
    ("010-1234 오육칠팔이요", "***-**** ****이요", "phone"),
    ("공일공 1234 5678이요", "*** **** ****이요", "phone"),
    ("공일공 1234 오육칠팔", "*** **** ****", "phone"),
    ("0101234 오육칠팔", "******* ****", "phone"),
    ("카드 1234 5678 구공일이 삼사오육", "카드 **** **** **** ****", "card"),
    # Two numbers with nothing between them that ends the run.
    ("공일공 일이삼사 오육칠팔 공일공 구팔칠육 오사삼이", "*** **** **** *** **** ****", "phone"),
    # A reading that covers the whole run must beat one that covers part of it
    # (#158 review, round 4). These are real bank layouts from privacy.py —
    # 3-2-6 and 3-3-6 — and the first group came out in the clear because a
    # narrower `phone` reading out-ranked the `account` reading that covered
    # every digit.
    ("공삼구 공구 공팔구오팔팔이에요", "*** ** ******이에요", "account"),
    ("육오이 공칠육 육사칠삼이공이에요", "*** *** ******이에요", "account"),
    # A full stop is a separator to privacy._SEP and was not one to the run.
    ("010.1234.오육칠팔", "***.****.****", "phone"),
    ("공일공.일이삼사.오육칠팔", "***.****.****", "phone"),
    ("010-1234.오육칠팔", "***-****.****", "phone"),
]


@pytest.mark.parametrize(("line", "masked", "category"), SPOKEN)
def test_a_number_read_aloud_is_masked_whole(
    line: str, masked: str, category: str, recogniser: SpokenNumberRecogniser
) -> None:
    """The whole point: `find_pii` sees no digits in most of these."""
    result = mask(line, recogniser=recogniser)
    assert result.text == masked
    assert set(result.counts) == {category}


@pytest.mark.parametrize(("line", "_masked", "_category"), SPOKEN)
def test_no_digit_of_the_number_survives(
    line: str, _masked: str, _category: str, recogniser: SpokenNumberRecogniser
) -> None:
    """Said the other way round, so a wrong expectation above cannot hide it.

    A digit written as a digit and a digit written as a syllable are the same
    digit; whichever script the transcript used, none of them may be left.
    """
    masked = mask(line, recogniser=recogniser).text
    span_free = masked.replace("이에요", "").replace("이랑", "").replace("입니다", "")
    span_free = span_free.replace("이요", "").replace("에요", "").replace("로 연락주세요", "")
    assert not any(c.isdigit() for c in span_free), masked
    for syllable in "공일이삼사오육칠팔구":
        assert syllable not in span_free.replace("카드", "").replace("계좌는 국민", "").replace(
            "주민번호", ""
        ).replace("번호", ""), masked


def test_the_particle_is_left_on_the_sentence(recogniser: SpokenNumberRecogniser) -> None:
    """Read greedily, the run eats the first syllable of what follows it.

    `이에요` begins with 이, which is also 2. The greedy reading made the phone
    number thirteen digits and no pattern matched it as a phone; for
    `오육칠팔구공이요` the greedy reading was a group of seven, wider than any
    bank layout, and **nothing matched at all**.
    """
    assert mask("공일공 일이삼사 오육칠팔이랑", recogniser=recogniser).text.endswith("이랑")
    assert mask("계좌는 국민 삼일이 이사 오육칠팔구공이요", recogniser=recogniser).text.endswith(
        "이요"
    )


# --- what it must not touch -----------------------------------------------

EVERYDAY = [
    "일이 있어서 못 갔어요",  # 일이 is "work", not 12
    "이사 준비 중입니다",  # 이사 is "moving house", not 24
    "삼일 걸립니다",
    "제일 먼저 할 일은",
    "이 사안은 구조적입니다",
    "사오일 정도 걸려요",
    "오늘 오후 세시 회의",
    "예산은 십오만 정도",  # a scale word: never substituted
    "삼십 퍼센트 올랐습니다",
    "일이 이사 삼사 사오 오육 육칠",  # long enough to rewrite, matches nothing
]


@pytest.mark.parametrize("line", EVERYDAY)
def test_ordinary_korean_is_not_a_number(line: str, recogniser: SpokenNumberRecogniser) -> None:
    """Over-masking is allowed by policy and still has a cost.

    Every one of these syllables is a digit in the table. What keeps them out of
    the transcript is that the patterns reject what they produce — the shortest
    shape accepted is a ten-digit account — not a list of exceptions.
    """
    assert mask(line, recogniser=recogniser).text == line


@pytest.mark.parametrize("scale_word", ["십", "백", "천", "만", "억"])
def test_a_scale_word_is_not_a_digit(scale_word: str) -> None:
    """십/백/천/만 are absent from the table on purpose (#148).

    Asserted against the table rather than through a sentence. Going through a
    sentence looked like it tested this and did not: the scale words that *are*
    absent break a run into pieces too short to rewrite, so adding 십 to the
    table changed no behaviour in any example here while leaving the rule
    broken. A rule this file states in a comment is worth one line that fails
    when somebody edits the table.
    """
    assert scale_word not in _DIGIT_SYLLABLES


def test_a_quantity_with_scale_words_is_left_alone(
    recogniser: SpokenNumberRecogniser,
) -> None:
    """The behaviour the line above protects.

    A value with a scale word in it is a quantity, and a meeting is full of
    them.
    """
    quantity = "십오만 삼천 이백 구십 일"
    assert _spell_out(quantity) == quantity
    assert mask(f"예산은 {quantity}이에요", recogniser=recogniser).counts == {}


def test_the_rewrite_stays_one_character_per_syllable() -> None:
    """Every value in the table is a single character.

    The whole design rests on the rewrite being length-preserving, and a
    two-character value (십 -> "10") would break it silently: spans would still
    be returned, and they would point at the wrong characters.
    """
    for syllable, digit in _DIGIT_SYLLABLES.items():
        assert len(syllable) == 1 and len(digit) == 1, syllable


# --- the properties the design rests on -----------------------------------


@pytest.mark.parametrize("line", [line for line, _, _ in SPOKEN] + EVERYDAY)
def test_rewriting_preserves_length(line: str) -> None:
    """Why no offset mapping exists.

    One syllable in, one digit out. A span found in the rewritten text is the
    same span in the original, so the span this recogniser returns needs no
    translation — which is where this kind of code usually goes wrong.
    """
    assert len(_spell_out(line)) == len(line)


def test_spans_are_over_the_original_text(recogniser: SpokenNumberRecogniser) -> None:
    line = "제 번호는 공일공 일이삼사 오육칠팔이에요"
    (start, end, category) = recogniser.find(line)[0]
    assert line[start:end] == "공일공 일이삼사 오육칠팔"
    assert category == "phone"


def test_a_short_run_is_left_alone() -> None:
    """The minimum is for legibility, not for safety.

    It is below the shortest thing any pattern accepts — nine digits, a phone
    number — so lowering it could not reveal a match. It only keeps the
    rewritten string readable when something goes wrong and somebody prints it.
    """
    assert _spell_out("이사") == "이사"
    assert MIN_RUN_DIGITS < 9
    assert len("공일공일이삼사오육칠팔") > MIN_RUN_DIGITS


# --- two leaks the review found ------------------------------------------


def test_two_numbers_in_one_run_are_both_covered(
    recogniser: SpokenNumberRecogniser,
) -> None:
    """A run is one run of digits, not one number.

    Two numbers read back to back have nothing between them that ends the run,
    and returning the best single span left the second one in the clear:

        공일공 일이삼사 오육칠팔 공일공 구팔칠육 오사삼이
          ->  *** **** **** 공일공 구팔칠육 오사삼이
    """
    line = "공일공 일이삼사 오육칠팔 공일공 구팔칠육 오사삼이"
    result = mask(line, recogniser=recogniser)
    assert result.counts == {"phone": 2}
    for syllable in "공일이삼사오육칠팔구":
        assert syllable not in result.text


def test_a_number_written_in_both_scripts_is_found(
    recogniser: SpokenNumberRecogniser,
) -> None:
    """Whisper does not pick one script for a whole number.

    Counting only syllables put both of these under the threshold — four spoken
    syllables in the first — and neither was masked at all.
    """
    assert mask("공일공 1234 5678이요", recogniser=recogniser).counts == {"phone": 1}
    assert "공일공" not in mask("공일공 1234 5678이요", recogniser=recogniser).text
    assert mask("010-1234 오육칠팔이요", recogniser=recogniser).counts == {"phone": 1}
    assert "오육칠팔" not in mask("010-1234 오육칠팔이요", recogniser=recogniser).text


def test_digits_beside_a_word_are_not_a_spoken_number(
    recogniser: SpokenNumberRecogniser,
) -> None:
    """The cost of letting a run contain digits, and what stops it.

    `버전 20260910 이사 갑니다` is one run once digits are allowed in: eight
    digits plus 이사, read as 24, matched the account shape. A run has to be
    spoken to be a spoken number.
    """
    for line in (
        "버전 20260910 이사 갑니다",
        "2026-09-10에 이사 갑니다",
        "IP 192.168.10.20 이요",
        "IP 192 168 10 20 이요",
        "주문번호 20260910 이 건은 보류입니다",
        "티켓 12345 이슈 67890 사항 확인",
    ):
        assert mask(line, recogniser=recogniser).text == line


# The syllables are hard against a digit, so the script switches inside a group.
GLUED = [
    ("010-1234-56칠팔", "***-****-****"),
    ("010 1234 567팔", "*** **** ****"),
    ("010-1234-5육칠팔", "***-****-****"),
    ("01012345육칠팔", "***********"),
]


@pytest.mark.parametrize(("line", "expected"), GLUED)
def test_a_tail_of_one_or_two_syllables_is_still_the_number(
    line: str, expected: str, recogniser: SpokenNumberRecogniser
) -> None:
    """`MIN_SPOKEN_SYLLABLES` was a claim about numbers, and it was false.

    Three syllables was written as "a script switch never happens for one
    syllable". It does, when the switch is inside a group rather than across a
    pause, and both ways it failed were bad:

        010-1234-56칠팔   nine digits, so no pattern matched and nothing was
                          masked -- `mask` and `find_unmasked` both silent
        010 1234 567팔    ten digits, read as an account, and the account rule
                          kept the last four: `*** ***4 567팔`

    The second is the one to keep a test on. It is masked, `counts` records a
    masked span, and four digits of a phone number are standing in the output --
    a leak that looks like a success from every direction except reading it.
    """
    masked = mask(line, recogniser=recogniser)
    assert masked.text == expected
    assert masked.counts == {"phone": 1}


def test_the_separator_is_what_tells_them_apart(
    recogniser: SpokenNumberRecogniser,
) -> None:
    """The same syllables, glued or spaced, and only one of them is a number.

    The same eight digits, one syllable, and only the glued one is a number.
    This is why the threshold could not simply be lowered to one: at one
    syllable across a separator, `버전 20260910 이사` comes back. Korean writes a
    number's groups without internal spaces and writes the next word with one,
    so the space carries the answer.

    **What this does not reach.** Spell the tail off on its own --
    `010 1234 567 팔` -- and the recogniser declines it, correctly, while the
    patterns still read the remaining `010 1234 567` as a ten-digit account and
    the account rule keeps `4567`. That is the same output for input with no
    syllable in it at all, so it is the account rule's last-four, not this
    threshold, and it lives in `packages/integrations`. Issue #182.
    """
    assert mask("010 1234 567팔", recogniser=recogniser).text == "*** **** ****"
    assert mask("버전 20260910 이사 갑니다", recogniser=recogniser).text == (
        "버전 20260910 이사 갑니다"
    )
    assert MIN_SPOKEN_SYLLABLES >= 3  # still what a run across a separator needs


@pytest.mark.parametrize(
    "line",
    [
        # Only 이 can be a particle's first syllable and also a digit; 에 and 요
        # are not digits and never enter the run. Giving back up to three
        # syllables handed real digits to the sentence: 일 (=1) and 공삼 (=03)
        # below. Whether the trailing 이 is masked with the number or left on
        # the sentence is rank's call for these -- neither is a leak.
        "공칠삼 육팔칠오 육구칠이일이에요",
        "공칠공 일팔삼오 칠구사오공삼이에요",
    ],
)
def test_a_trailing_digit_is_not_mistaken_for_a_particle(
    line: str, recogniser: SpokenNumberRecogniser
) -> None:
    result = mask(line, recogniser=recogniser)
    for syllable in "공일삼사오육칠팔구":
        assert syllable not in result.text
    assert result.counts, "nothing was masked"


def test_a_run_no_pattern_covers_is_masked_exactly_as_if_written(
    recogniser: SpokenNumberRecogniser,
) -> None:
    """Nineteen digits with a hyphen after the eighth: not a card, not the
    catch-all (which wants twelve *contiguous*), so `account` takes the tail
    and the head stays -- **written or spoken alike.** The recogniser's contract
    is parity with the written form, not more than it; what the patterns cannot
    cover is #143's and #148's, in `packages/integrations`.

    What must not happen is the old behaviour: three real digits handed back
    to the sentence as if 육팔일 could begin a particle.
    """
    spoken = mask("삼삼구공칠오칠구-팔삼사칠공일칠사육팔일", recogniser=recogniser)
    written = mask("33907579-83470174681")
    assert spoken.counts == written.counts == {"account": 1}
    # The same span in both: the head is what neither can cover, the tail is
    # what `account` claims. (Spoken masks its tail whole; written keeps four --
    # the mixed-script rule in masking.py, and the safer of the two.)
    assert spoken.text == "삼삼구공칠오칠구-***********"
    assert written.text == "33907579-*******4681"


def test_the_more_specific_reading_wins(recogniser: SpokenNumberRecogniser) -> None:
    """`010 1234 5678` is both a phone number and an account-shaped run.

    Reported as `account` the count in `aud_masking_events` would say the
    meeting contained a bank account. The readings are ranked by how specific
    the pattern that claimed them is.
    """
    assert recogniser.find("공일공 일이삼사 오육칠팔이에요")[0][2] == "phone"


@pytest.mark.parametrize("line", [line for line, _, _ in SPOKEN])
def test_the_outbound_guard_is_satisfied(line: str, recogniser: SpokenNumberRecogniser) -> None:
    """The other direction: would `packages/integrations` let this out?

    The guard does not read spoken numerals either, so it passes these lines
    whether or not the masker did its job. This asserts the masker's output, not
    the guard's opinion of the input.
    """
    masked = mask(line, recogniser=recogniser).text
    assert find_unmasked(masked) == []
    assert "공일공" not in masked


# --- the seam -------------------------------------------------------------


def test_the_fake_returns_what_it_was_given() -> None:
    fake = FakeRecogniser([(0, 3, "name")])
    assert fake.find("아무거나") == [(0, 3, "name")]


def test_the_setting_can_turn_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """`none` is how the harness measures the patterns on their own."""
    monkeypatch.setenv("AUTUNE_AUDIO_RECOGNISER", "none")
    get_recogniser.cache_clear()
    from autune_audio.config import get_settings

    get_settings.cache_clear()
    assert isinstance(get_recogniser(), FakeRecogniser)
    get_recogniser.cache_clear()
    get_settings.cache_clear()


def test_an_unknown_setting_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """And refused when the settings load, not when the recogniser is first built.

    `recogniser` is a `Literal`, so a typo in the environment fails the process
    that reads it rather than the first task that masks something. A worker that
    starts and then cannot mask is worse than one that does not start.
    """
    from pydantic import ValidationError

    from autune_audio.config import get_settings

    monkeypatch.setenv("AUTUNE_AUDIO_RECOGNISER", "klue_ner")
    get_recogniser.cache_clear()
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="spoken_numbers"):
        get_settings()
    get_recogniser.cache_clear()
    get_settings.cache_clear()
