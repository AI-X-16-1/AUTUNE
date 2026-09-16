"""Scoring Korean speech recognition.

Korean needs different metrics from English, and using the English ones makes a
model look worse than it is.

**CER, not WER.** Korean is agglutinative: one 어절 carries a stem and its
particles, so a single wrong syllable fails a whole word. `진행하겠습니다` heard
as `진행하겠읍니다` is WER 1.0 and CER 0.08, and the second number is the one
that describes what a reader would notice. WER stays available for comparing
against English-language literature; CER is what this module is judged on.

**Normalisation is applied to both sides, and scored twice.** The gap between
the raw and normalised score is what post-processing can recover; what remains
is the model. Reporting only one of them hides which is which.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .alignment import count, edit_ops

_PUNCT = re.compile(r"[.,!?;:~…·\"'“”‘’()\[\]<>《》「」『』]")
_SPACE = re.compile(r"\s+")
# Hesitation forms, listed from what speakers actually produced in the module A
# evaluation recording. The list is empirical and therefore incomplete: a filler
# that is not here survives in the hypothesis while its listed neighbours are
# stripped from the reference, which *raises* CER after normalisation. When a
# normalised score is worse than its raw score, suspect this list first.
_FILLER = re.compile(r"(?<!\S)(음+|어+|아+|그+|저기|뭐지|뭐더라|뭐였더라|그러니까)(?!\S)")

# Units the model writes in latin script where the speaker said them in Korean.
# Both spellings are correct; scoring them as errors measures orthography, not
# recognition. On the evaluation recording this mapping alone took S1 from
# CER 0.078 to 0.035 -- two thirds of the remaining error was spelling.
_SPOKEN_UNITS = (
    ("퍼센트포인트", "%포인트"),
    ("센티미터", "cm"),
    ("킬로헤르츠", "khz"),
    ("메가헤르츠", "mhz"),
    ("기가바이트", "gb"),
    ("퍼센트", "%"),
)

# Sino-Korean numerals as they are spoken. Whisper writes dates either way, and
# a due-date parser reads only one of them.
_SINO = {
    "영": 0,
    "공": 0,
    "일": 1,
    "이": 2,
    "삼": 3,
    "사": 4,
    "오": 5,
    "육": 6,
    "칠": 7,
    "팔": 8,
    "구": 9,
}
_UNITS = {"십": 10, "백": 100, "천": 1000}


def _sino_to_int(text: str) -> int | None:
    """`십팔` -> 18. None when the text is not a sino-Korean numeral."""
    if not text or any(c not in _SINO and c not in _UNITS for c in text):
        return None
    total, current = 0, 0
    for char in text:
        if char in _SINO:
            current = _SINO[char]
        else:
            current = current or 1
            total += current * _UNITS[char]
            current = 0
    return total + current


_DIGITS = "영공일이삼사오육칠팔구십백천"

# A numeral is only a numeral when a unit follows it, and only when a Hangul
# syllable does not precede it -- 제일 is not 제1.
#
# 원 is deliberately absent. As a one-character lookahead it turns 공원 into 0원
# and 사원 into 4원, and those words are far more common in a meeting than a sum
# in won spelled out in Hangul. 달러 is safe because it is two characters.
#
# 만 and 억 stay as they are, and the numerals around them are converted, so
# 백팔십억 becomes 180억 rather than 18000000000. That is the form the model
# writes, and it keeps a number's weight in CER proportional to how long it is
# said -- full expansion would make one misheard figure cost eleven characters.
#
# A space is allowed only before the multi-character units, where it cannot cost
# anything: 삼십 % is a percentage, but 이 시점 is not two o'clock.
_NUMERAL = re.compile(rf"(?<![가-힣])[{_DIGITS}]+(?=[월일시분초년개번호만억]|\s?%|\s?달러)")

# Digit-group separators. 2,475 and 2475 are the same number said aloud.
_GROUPED = re.compile(r"(?<=\d),(?=\d)")


def normalise(text: str) -> str:
    """Put both sides of a comparison in the same shape.

    Punctuation out, whitespace collapsed, latin lowercased, spoken numerals
    turned into digits, spoken units written in latin, fillers dropped. Applied
    identically to reference and hypothesis — a normalisation that touches only
    one side flatters the model.

    It can also make a score worse, and that is information rather than a bug:
    every rule here is a claim that some difference does not matter, and a rule
    whose word list is incomplete removes text from one side only. Record the
    raw score next to the normalised one so the two stay separable.
    """
    # Before punctuation goes, because otherwise 2,475 becomes 2 475.
    text = _GROUPED.sub("", text)
    text = _PUNCT.sub(" ", text)
    # Units are written before numerals are read, so that 삼십 퍼센트 has become
    # 삼십 % by the time the numeral looks for the unit that follows it.
    for spoken, written in _SPOKEN_UNITS:
        text = text.replace(spoken, written)
    text = _NUMERAL.sub(lambda m: str(_sino_to_int(m.group()) or m.group()), text)
    text = _FILLER.sub(" ", text)
    # 30 % and 30% read the same. CER drops spaces anyway; this keeps normalise
    # itself canonical, so equal output means the two were read the same way.
    text = re.sub(r"(?<=\d)\s+%", "%", text)
    return _SPACE.sub(" ", text.lower()).strip()


@dataclass(frozen=True)
class CharacterErrorRate:
    cer: float
    substitutions: int
    deletions: int
    insertions: int
    reference_characters: int

    def __repr__(self) -> str:
        """Counts only — a transcript is meeting content."""
        return (
            f"CharacterErrorRate(cer={self.cer:.4f}, S={self.substitutions}, "
            f"D={self.deletions}, I={self.insertions}, N={self.reference_characters})"
        )


def character_error_rate(reference: str, hypothesis: str) -> CharacterErrorRate:
    """Levenshtein distance over characters, spaces removed.

    Spaces are dropped because Korean spacing is inconsistent between speakers
    and between models, and counting it as error measures the wrong thing.
    """
    ref = [c for c in reference if not c.isspace()]
    hyp = [c for c in hypothesis if not c.isspace()]
    if not ref:
        raise ValueError("reference is empty; character error rate is undefined")

    substitutions, deletions, insertions = count(edit_ops(ref, hyp))
    return CharacterErrorRate(
        cer=(substitutions + deletions + insertions) / len(ref),
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
        reference_characters=len(ref),
    )


@dataclass(frozen=True)
class Hallucination:
    """What a model wrote where nobody spoke.

    Whisper invents subtitle boilerplate over silence — "시청해주셔서
    감사합니다" and its relatives. A read-aloud benchmark never shows it,
    because there is no silence in one. Drills 01 and 02 of the recording cue
    sheet exist to make it visible, and the answer there is the empty string.
    """

    characters: int
    text_appeared: bool

    def __repr__(self) -> str:
        """Never the invented text: it reads like a real transcript in a log."""
        return f"Hallucination(characters={self.characters}, appeared={self.text_appeared})"


def hallucinated_characters(hypothesis: str) -> Hallucination:
    """Score a segment whose reference is silence. Anything at all is a failure."""
    stripped = _SPACE.sub("", _PUNCT.sub("", hypothesis))
    return Hallucination(characters=len(stripped), text_appeared=bool(stripped))


@dataclass(frozen=True)
class TermAccuracy:
    accuracy: float
    expected: int
    correct: int
    missed: tuple[str, ...]

    def __repr__(self) -> str:
        """Missed terms are named: they are our vocabulary, not meeting content."""
        return (
            f"TermAccuracy(accuracy={self.accuracy:.4f}, {self.correct}/{self.expected}, "
            f"missed={list(self.missed)})"
        )


def term_accuracy(hypothesis: str, expected_terms: list[str]) -> TermAccuracy:
    """How many domain terms survived transcription, spelled as we spell them.

    Separate from CER because the cost is different in kind. A wrong particle
    costs readability; `pyannote` heard as `파이어노트` costs an action item,
    because the extractor downstream keys on the term. A model can score well on
    CER and be unusable here.

    Matching ignores case and spacing — `Next.js` and `next js` are the same
    term said aloud — but not spelling.
    """
    if not expected_terms:
        raise ValueError("no terms given; accuracy is undefined")

    def flatten(text: str) -> str:
        return _SPACE.sub("", text.lower().replace(".", "").replace("-", ""))

    haystack = flatten(hypothesis)
    missed = tuple(term for term in expected_terms if flatten(term) not in haystack)
    correct = len(expected_terms) - len(missed)
    return TermAccuracy(
        accuracy=correct / len(expected_terms),
        expected=len(expected_terms),
        correct=correct,
        missed=missed,
    )
