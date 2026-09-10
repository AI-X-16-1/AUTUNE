"""The vocabulary a meeting is about to use, assembled for one transcription.

Evaluation 01 measured the problem this exists for: the calibration session
scored CER 3.5% and the terminology session 30.7%, same speaker and same
microphone, the only variable being English proper nouns. Term accuracy was
10/31. Not one of the libraries this project is built on survived — `pyannote`
came back as 파이노트, `faster-whisper` as 페이스터 위시퍼.

That is not a readability problem. Module B keys action items on entity names,
so a mangled term loses the item attached to it.

**The glossary is per meeting, not global.** Whisper's prompt window is 224
tokens; a company-wide vocabulary does not fit and would dilute what does. So
this assembles a small list from the sources module A already owns and spends
the budget in priority order.

See docs/modules/audio-evaluations/01-baseline-large-v3.md and issue #118.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

# Whisper reserves half its context for the prompt: `max_length // 2 - 1`, which
# is 223 for every model we run. Kept a little under, because the count here is
# over our own terms and the framing sentence is tokenised too.
PROMPT_TOKEN_BUDGET = 200

# Korean is roughly one token per syllable and latin terms run two to four
# tokens each. Whisper's tokeniser is not importable without loading a model, so
# this estimates rather than counts, and estimates high: overrunning the window
# drops terms silently, and dropping one we thought we sent is the failure this
# module exists to prevent.
_TOKENS_PER_LATIN_WORD = 4
_TOKENS_PER_HANGUL_CHAR = 1


def estimate_tokens(text: str) -> int:
    """A deliberate over-estimate of what Whisper will make of ``text``."""
    hangul = sum(1 for c in text if "가" <= c <= "힣")
    words = len([w for w in text.split() if any(c.isascii() and c.isalnum() for c in w)])
    return hangul * _TOKENS_PER_HANGUL_CHAR + words * _TOKENS_PER_LATIN_WORD


# The stack this project is actually built on, which is what the recording
# showed the model cannot spell. Ordered least to most important, because
# `initial_prompt` is truncated from the *front*: `previous_tokens[-(n):]` keeps
# the tail, so anything that has to survive goes last.
PROJECT_TERMS: tuple[str, ...] = (
    "Tailwind",
    "Next.js",
    "PostgreSQL",
    "FastAPI",
    "Celery",
    "pgvector",
    "Prophet",
    "XGBoost",
    "SetFit",
    "BM25",
    "PageRank",
    "spaCy",
    "NER",
    "NLI",
    "DER",
    "WER",
    "CER",
    "RTF",
    "int8",
    "Sentence-BERT",
    "cross-encoder",
    "DeBERTa",
    "CTranslate2",
    "ECAPA-TDNN",
    "silero-VAD",
    "speaker embedding",
    "large-v3-turbo",
    "faster-whisper",
    "Whisper",
    "pyannote",
)
"""Every term evaluation 01 caught the model mangling, plus the rest of the
stack. `pyannote` is last on purpose: it was mangled three different ways
(파이노트 · 파이어노트 · 하이에노트) and it is the one module C and D key on."""


def build_prompt(
    *,
    participants: Sequence[str] = (),
    corrections: Sequence[str] = (),
    terms: Sequence[str] = PROJECT_TERMS,
    budget: int = PROMPT_TOKEN_BUDGET,
) -> str:
    """One meeting's glossary, as the sentence handed to Whisper.

    Priority runs the other way from the text: what matters most is emitted
    last, because the front of an over-long prompt is what gets dropped.

    - ``participants`` are the most expensive to lose. A wrong name does not
      look wrong, and module B maps assignees by name — 박준호 heard as 박준우
      silently drops the action item's owner. Two of four names were wrong in
      evaluation 01.
    - ``corrections`` are terms a user has already fixed by hand in this team's
      transcripts. Somebody told us the model got these wrong; that is stronger
      evidence than our own guess at what matters.
    - ``terms`` is the project stack.

    Returns an empty string when there is nothing to say, so a caller can pass
    the result straight through — Whisper treats "" and None alike, and a
    special case here would be one the caller has to remember.
    """
    ordered = _dedupe(terms, corrections, participants)
    if not ordered:
        return ""

    prefix = "회의 녹취록. 사용 용어: "
    kept: list[str] = []
    used = estimate_tokens(prefix)
    # Backwards, so that a budget too small to hold everything drops the terms
    # we would rather lose rather than the ones we would not.
    for term in reversed(ordered):
        cost = estimate_tokens(term) + 1  # the separator
        if used + cost > budget:
            continue
        kept.append(term)
        used += cost

    kept.reverse()
    return prefix + ", ".join(kept) + "."


def _dedupe(*sources: Iterable[str]) -> list[str]:
    """Flatten the sources into one list, case-insensitively unique.

    Across sources as well as within them: a term that is both in the stack list
    and in this team's corrections must not be paid for twice.

    A repeat keeps the **last** occurrence — its position and its spelling.
    Sources arrive least-important first, so the later one is the one that
    outranks: if a user has corrected a term by hand, their spelling of it beats
    ours, and the term is placed where the higher-priority source put it.
    """
    flat = [value.strip() for source in sources for value in source]
    seen: set[str] = set()
    out: list[str] = []
    for value in reversed(flat):
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    out.reverse()
    return out
