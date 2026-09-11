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
    MIN_RUN_SYLLABLES,
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
# (line, category, the part that must not survive)
SPOKEN = [
    ("제 번호는 공일공 일이삼사 오육칠팔이에요", "phone", "공일공 일이삼사 오육칠팔"),
    ("공일공 일이삼사 오육칠팔이랑", "phone", "공일공 일이삼사 오육칠팔"),
    ("번호 공일공 일이삼사 오육칠팔입니다", "phone", "공일공 일이삼사 오육칠팔"),
    ("공일공 일이삼사 오육칠팔로 연락주세요", "phone", "공일공 일이삼사 오육칠팔"),
    ("공일공일이삼사오육칠팔", "phone", "공일공일이삼사오육칠팔"),
    ("계좌는 국민 삼일이 이사 오육칠팔구공이요", "account", "삼일이 이사 오육칠팔구공"),
    ("주민번호 구공공일공일 일이삼사오육칠이에요", "rrn", "구공공일공일 일이삼사오육칠"),
]


@pytest.mark.parametrize(("line", "category", "_secret"), SPOKEN)
def test_a_number_read_aloud_is_found(
    line: str, category: str, _secret: str, recogniser: SpokenNumberRecogniser
) -> None:
    """The whole point: `find_pii` sees no digits in any of these."""
    assert mask(line).counts == {}, "the patterns alone should find nothing here"
    assert mask(line, recogniser=recogniser).counts == {category: 1}


@pytest.mark.parametrize(("line", "_category", "secret"), SPOKEN)
def test_the_number_itself_does_not_survive(
    line: str, _category: str, secret: str, recogniser: SpokenNumberRecogniser
) -> None:
    """`_hide` covers every non-separator character in a numeric span.

    That behaviour was written for exactly this input — a span whose characters
    are syllables rather than digits — and this is the test that uses it. What
    must survive is the sentence around the number, including the particle; what
    must not is any part of the number itself.
    """
    assert secret not in mask(line, recogniser=recogniser).text


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

    It is below the shortest thing any pattern accepts, so lowering it could not
    reveal a match — it only keeps the rewritten string readable when something
    goes wrong and somebody prints it.
    """
    assert _spell_out("이사") == "이사"
    assert len("공일공일이삼사오육칠팔") > MIN_RUN_SYLLABLES


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
    monkeypatch.setenv("AUTUNE_AUDIO_RECOGNISER", "klue_ner")
    get_recogniser.cache_clear()
    from autune_audio.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(ValueError, match="unknown AUTUNE_AUDIO_RECOGNISER"):
        get_recogniser()
    get_recogniser.cache_clear()
    get_settings.cache_clear()
