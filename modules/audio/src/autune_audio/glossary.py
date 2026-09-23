"""The vocabulary a meeting is about to use, assembled for one transcription.

Evaluation 01 measured the problem this exists for: the calibration session
scored CER 3.5% and the terminology session 30.7%, same speaker and same
microphone, the only variable being English proper nouns. Term accuracy was
10/31. Not one of the libraries this project is built on survived — `pyannote`
came back as 파이노트, `faster-whisper` as 페이스터 위시퍼.

That is a readability problem, and a search one: module D's BM25 side
tokenises 파이노트 and pyannote differently, so two meetings about the same
thing do not link. It is not a lost action item -- module B takes assignees
from speaker attribution and does not key on entity names (evaluation 01,
section 3.1; an earlier version of this comment said otherwise).

**The glossary is per meeting, not global.** Whisper's prompt window is 224
tokens; a company-wide vocabulary does not fit and would dilute what does. So
this assembles a small list from the sources module A already owns and spends
the budget in priority order.

See docs/modules/audio-evaluations/01-baseline-large-v3.md and issue #118.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from .config import get_settings

# Whisper reserves half its context for the prompt: `max_length // 2 - 1`, which
# is 223 for every model we run. Kept a little under, because the count here is
# over our own terms and the framing sentence is tokenised too.
PROMPT_TOKEN_BUDGET = 200

# Whisper's tokeniser is not importable without loading a model, so this
# estimates. It must estimate **high**: the window drops what does not fit, in
# silence, and dropping a term we believed we had sent is the failure this
# module exists to prevent.
#
# An earlier version counted whitespace-separated words and came out 28-56%
# under, because BPE does not treat a hyphenated compound as one word:
# ECAPA-TDNN is eight tokens, not four. Separators are word boundaries here for
# the same reason they are to the tokeniser.
_TOKENS_PER_LATIN_PIECE = 4
_TOKENS_PER_HANGUL_CHAR = 2

_PIECE = re.compile(r"[A-Za-z0-9]+")


def estimate_tokens(text: str) -> int:
    """An upper bound on what Whisper will make of ``text``.

    Verified against ``large-v3``'s own tokeniser; every term in
    ``PROJECT_TERMS`` and every name tried came out at or below this number.
    Being wasteful costs a low-priority term; being optimistic costs whichever
    term the window happens to cut.
    """
    hangul = sum(1 for c in text if "가" <= c <= "힣")
    pieces = len(_PIECE.findall(text))
    return hangul * _TOKENS_PER_HANGUL_CHAR + pieces * _TOKENS_PER_LATIN_PIECE


# The stack this project is actually built on, which is what the recording
# showed the model cannot spell. Ordered least to most important; ``build_prompt``
# decides which end that has to come out of.
PROJECT_TERMS: tuple[str, ...] = (
    "Tailwind",
    "Next.js",
    "Google Calendar",
    "Bolt for Python",
    "betweenness",
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
    mode: str | None = None,
    budget: int = PROMPT_TOKEN_BUDGET,
) -> str:
    """One meeting's glossary, in the order the chosen channel will not cut.

    Priority, least to most:

    - ``terms`` is the project stack.
    - ``corrections`` are terms a user has already fixed by hand in this team's
      transcripts. Somebody told us the model got these wrong; that is stronger
      evidence than our own guess at what matters.
    - ``participants`` are the term class every meeting has and no static
      list can hold. A wrong name does not look wrong, and names are not
      masked, so the reader and module D's lexical linking both see the
      misspelling. Module B does not: assignees come from speaker
      attribution, not from the name in the text (evaluation 01, section
      3.1).

    ``mode`` defaults to ``AUTUNE_AUDIO_GLOSSARY_MODE``, so the glossary is
    always built for the channel it will actually travel on.

    **Which end that comes out of depends on ``mode``, because the two channels
    truncate from opposite ends.** ``hotwords`` keeps its first 223 tokens and
    drops the rest; ``initial_prompt`` lands in ``previous_tokens``, which keeps
    the *last* 223. Emitting in one fixed order would put the names exactly
    where the default channel cuts.

    ``both`` follows ``hotwords``: one of the two has to be wrong, and the
    measurement in issue #118 has ``hotwords`` doing the work.

    **The framing sentence goes on both channels**, which is not obvious for
    ``hotwords``: that one is a list of words to boost, and the prefix costs ten
    tokens and boosts 회의 and 녹취록 along with the terms. It was removed for
    exactly that reason and the measurement put it back — term accuracy on the
    11m37s recording was 45% without it against 62% with it, same terms and same
    order. ``hotwords`` is prepended after ``sot_prev``, so the model reads it as
    previous text, and a bare comma-separated list of English words is not what
    a transcript looks like. The Korean framing is what makes it one.

    Returns an empty string when there is nothing to say, so a caller can pass
    the result straight through — Whisper treats "" and None alike, and a
    special case here would be one the caller has to remember.
    """
    # Read from settings rather than defaulted here: a default would be a second
    # place the channel is decided, and the two would drift the first time
    # anyone changed the setting. The glossary would then be built for the end
    # that gets cut, silently.
    mode = mode if mode is not None else get_settings().glossary_mode

    ordered = _dedupe(terms, corrections, participants)
    if not ordered:
        return ""

    prefix = "회의 녹취록. 사용 용어: "
    kept: list[str] = []
    used = estimate_tokens(prefix)
    # Most important first while packing, so a budget too small to hold
    # everything drops the terms we would rather lose.
    for term in reversed(ordered):
        cost = estimate_tokens(term) + 1  # the separator
        if used + cost > budget:
            continue
        kept.append(term)
        used += cost

    if mode == "prompt":
        # This channel keeps the tail, so what has to survive is emitted last.
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
