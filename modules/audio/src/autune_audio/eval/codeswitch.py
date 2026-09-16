"""Scoring Korean-English code-switched speech, the way HiKE scores it.

HiKE (ThetaOne-AI/HiKE, EACL Findings 2026) is the benchmark this module uses
for the one thing the in-house recording cannot measure: how well the model
survives a language switch mid-sentence. Its two metrics are reimplemented
here rather than imported, because HiKE ships them as a fork of ``jiwer``
inside a git submodule, and both definitions are short.

**MER, mixed error rate.** Word error rate over a mixed tokenisation: every
Hangul syllable is a token, every latin word is a token. It is CER for the
Korean and WER for the English in one number, which is what the paper reports.

**PIER, point-of-interest error rate.** Word error rate counted only at the
words the annotators tagged around each switch, ``<tag bug> <tag 는>``. A model
that transcribes the Korean perfectly and drops every English word scores well
on MER and badly on PIER, and PIER is the one that predicts whether an action
item keyed on that word survives.

**Loanwords.** HiKE labels every loanword with both spellings, and scores
``버그`` and ``bug`` as the same word by rewriting the Korean spelling to the
English one on both sides before comparing. The same rewrite is applied here;
``korean.term_accuracy`` accepts the same equivalence through ``aliases``.

Normalisation follows HiKE, not ``korean.normalise``: lowercase, punctuation
out, whitespace collapsed, and nothing else. Spoken numerals are left as the
model wrote them, because the paper's numbers were produced that way and a
score that cannot sit in its table is not worth having.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from .alignment import count, edit_ops

_HANGUL = re.compile(r"[가-힣]")
_SPACE = re.compile(r"\s+")

Loanwords = Iterable[tuple[str, str]]
"""(Korean spelling, English spelling) pairs, as HiKE labels them."""


def fold_loanwords(text: str, loanwords: Loanwords) -> str:
    """Rewrite each loanword's Korean spelling to its English one.

    Plain substring replacement, as HiKE does it, so ``버그는`` becomes ``bug는``
    with the particle still attached; tokenisation separates them afterwards.
    """
    for korean, english in loanwords:
        text = text.replace(korean, english)
    return text


def _normalise(text: str) -> str:
    """HiKE's normalisation: lowercase, punctuation removed, spaces collapsed."""
    kept = (c if not unicodedata.category(c).startswith("P") else " " for c in text)
    return _SPACE.sub(" ", "".join(kept).lower()).strip()


def mixed_tokens(text: str) -> list[str]:
    """Hangul syllable by syllable, everything else word by word.

    Mirrors HiKE's ``SpaceKoreanChars``: a space on either side of every Hangul
    syllable, then split on whitespace.
    """
    return _HANGUL.sub(lambda m: f" {m.group()} ", text).split()


@dataclass(frozen=True)
class MixedErrorRate:
    mer: float
    substitutions: int
    deletions: int
    insertions: int
    reference_tokens: int

    def __repr__(self) -> str:
        """Counts only — a transcript is meeting content."""
        return (
            f"MixedErrorRate(mer={self.mer:.4f}, S={self.substitutions}, "
            f"D={self.deletions}, I={self.insertions}, N={self.reference_tokens})"
        )


def mixed_error_rate(
    reference: str, hypothesis: str, *, loanwords: Loanwords = ()
) -> MixedErrorRate:
    """Levenshtein distance over mixed tokens, divided by the reference length."""
    loanwords = tuple(loanwords)
    ref = mixed_tokens(_normalise(fold_loanwords(reference, loanwords)))
    hyp = mixed_tokens(_normalise(fold_loanwords(hypothesis, loanwords)))
    if not ref:
        raise ValueError("reference is empty; mixed error rate is undefined")

    substitutions, deletions, insertions = count(edit_ops(ref, hyp))
    return MixedErrorRate(
        mer=(substitutions + deletions + insertions) / len(ref),
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
        reference_tokens=len(ref),
    )


_TAG_OR_WORD = re.compile(r"<tag\s+([^>]+?)\s*>|(\S+)")
_LATIN_BEFORE_HANGUL = re.compile(r"([A-Za-z0-9]+)(?=[\uAC00-\uD7A3])")


@dataclass(frozen=True)
class PointOfInterestErrorRate:
    pier: float
    substitutions: int
    deletions: int
    insertions: int
    poi_words: int

    def __repr__(self) -> str:
        """Counts only — a transcript is meeting content."""
        return (
            f"PointOfInterestErrorRate(pier={self.pier:.4f}, S={self.substitutions}, "
            f"D={self.deletions}, I={self.insertions}, POI={self.poi_words})"
        )


def _tagged_words(labeled: str) -> tuple[list[str], set[int]]:
    """Reference words with the tags removed, and the indices that were tagged."""
    words: list[str] = []
    poi: set[int] = set()
    for match in _TAG_OR_WORD.finditer(labeled):
        tagged, plain = match.groups()
        pieces = _normalise(tagged if tagged is not None else plain).split()
        if tagged is not None:
            poi.update(range(len(words), len(words) + len(pieces)))
        words.extend(pieces)
    return words, poi


def point_of_interest_error_rate(
    reference_labeled: str, hypothesis: str, *, loanwords: Loanwords = ()
) -> PointOfInterestErrorRate:
    """Word error rate counted only at the tagged words.

    ``reference_labeled`` is HiKE's ``text_pier_labeled``: whitespace-separated
    words with the points of interest wrapped as ``<tag word>``. An edit counts
    when the reference position it touches is tagged; an insertion is placed on
    the word it precedes, so one after the final word never counts, which is
    how HiKE's ``pier_fixed`` behaves once its dummy end token is appended.
    """
    loanwords = tuple(loanwords)
    ref, poi = _tagged_words(fold_loanwords(reference_labeled, loanwords))
    if not poi:
        raise ValueError("reference has no <tag> words; PIER is undefined")

    # The annotators wrote ``bug 는``; the model writes ``bug는``.
    hyp = _LATIN_BEFORE_HANGUL.sub(r"\1 ", _normalise(fold_loanwords(hypothesis, loanwords)))
    substitutions, deletions, insertions = count(
        edit for edit in edit_ops(ref, hyp.split()) if edit[1] in poi
    )
    return PointOfInterestErrorRate(
        pier=(substitutions + deletions + insertions) / len(poi),
        substitutions=substitutions,
        deletions=deletions,
        insertions=insertions,
        poi_words=len(poi),
    )
