"""How a decision moved between two versions: NLI both ways, plus lexical cues.

Forward NLI alone (earlier statement as premise) got half of the evaluation
set's change types wrong. What each step here corrects
(docs/modules/context.md, "Decision lineage"):

- **A restatement in other words reads as neutral forward.** "금요일에는
  배포하지 않기로 했다" does not strictly entail its paraphrase with an extra
  clause, but the paraphrase entails *it*. Entailment in either direction is
  what "nothing anyone would act on differs" looks like --
- **-- unless the extra clause is a new condition.** "재택근무는 주 2회" ->
  "주 2회 재택근무는 입사 3개월이 지난 직원에게만" is entailed backward exactly
  like a paraphrase. What gives it away is a restricting or adding particle
  (만, 도, 까지) on something the earlier statement never mentioned.
- **Reaffirming a changed state reads as contradicting the change.** After
  "A사 대신 B사로 바꾸기로", "B사를 계속 쓰기로" contradicts the *act* of
  switching. With the keep-words (계속, 그대로, 유지) taken out, what is left --
  "B사를 쓰기로" -- is entailed by the switch; a real revert ("A사를 계속")
  still contradicts it.
- **A moved parameter and a withdrawn decision are both contradictions**, at
  ~1.00 either way, so NLI carries no signal to tell them apart. What does: a
  withdrawal or replacement is *said* with negation or a stop/cancel/replace
  word, and a moved parameter is not.

The cue lists are general Korean negation, cancellation and restriction
vocabulary, not words lifted from the evaluation set. A modification phrased
with a negation ("주 1회로 줄이고 월요일은 하지 않는다") reads as reversed -- a
known limit of a lexical cue.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from autune_contracts import ChangeType

if TYPE_CHECKING:
    from autune_context.pipeline.base import NliScores

_ENTAILMENT_MIN = 0.5
_CONTRADICTION_MIN = 0.5

# (form, base tag) pairs kiwipiepy emits for a negated or stopped predicate.
# Base tag: kiwipiepy suffixes irregular conjugations ("VV-R", "VA-I").
_REVERSAL_TOKENS = {
    ("않", "VX"),  # -지 않다
    ("말", "VX"),  # -지 말다
    ("말", "VV"),  # A 말고 B
    ("안", "MAG"),  # 안 하다
    ("못", "MAG"),  # 못 하다
    ("없", "VA"),  # 없다, 없던 일로
    ("없이", "MAG"),  # A 없이
    ("아니", "VCN"),  # A가 아니라 B
    ("그만두", "VV"),
    ("접", "VV"),  # 사업을 접다
    ("없애", "VV"),
    ("멈추", "VV"),
}
# Nouns that withdraw or replace a decision without a negated predicate.
# Matched after joining a noun with its derivational suffix (백지 + 화).
_REVERSAL_NOUNS = {
    "취소",
    "철회",
    "중단",
    "중지",
    "폐지",
    "보류",
    "백지화",
    "무산",
    "포기",
    "번복",
    "대신",
}
# Particles that narrow (만), add (도) or extend (까지) what a decision covers.
_CONDITION_PARTICLES = {"만", "도", "까지"}
# Bound expressions restate a threshold ("16 이상만" == "최소 16"), not add one.
_BOUND_NOUNS = {"이상", "이하", "미만", "초과"}
# Adverbs that say "no change from now" -- stripped before re-asking NLI.
_KEEP_ADVERBS = {"계속", "계속해서", "그대로", "여전히", "변함없이"}


def classify_change(
    forward: NliScores,
    backward: NliScores,
    current: str,
    previous: str,
    kept_forward: NliScores | None = None,
) -> tuple[ChangeType, float]:
    """``forward`` scores (previous -> current), ``backward`` (current ->
    previous), ``kept_forward`` (previous -> ``strip_keep_words(current)``,
    when ``current`` had any). Returns the change type and the NLI score
    that decided it."""
    entailment = max(forward.entailment, backward.entailment)
    if entailment >= _ENTAILMENT_MIN:
        if forward.entailment < _ENTAILMENT_MIN and adds_condition(current, previous):
            return ChangeType.MODIFIED, backward.entailment
        return ChangeType.UNCHANGED, entailment
    if kept_forward is not None and kept_forward.entailment >= _ENTAILMENT_MIN:
        return ChangeType.UNCHANGED, kept_forward.entailment
    contradiction = max(forward.contradiction, backward.contradiction)
    if contradiction >= _CONTRADICTION_MIN:
        kind = ChangeType.REVERSED if marks_reversal(current) else ChangeType.MODIFIED
        return kind, contradiction
    return ChangeType.MODIFIED, forward.neutral


def marks_reversal(statement: str) -> bool:
    """Whether ``statement`` negates, stops, or cancels/replaces something."""
    if "더 이상" in statement:
        return True
    for form, tag in _morphemes(statement):
        if (form, tag) in _REVERSAL_TOKENS:
            return True
        if tag.startswith("NN") and form in _REVERSAL_NOUNS:
            return True
    return False


def adds_condition(current: str, previous: str) -> bool:
    """Whether ``current`` narrows, adds to or extends something ``previous``
    never mentioned: a 만/도/까지 whose head noun is new."""
    known = {form for form, tag in _morphemes(previous) if _is_content(tag)}
    head: str | None = None
    for form, tag in _morphemes(current):
        if _is_content(tag):
            head = form
        elif tag == "JX" and form in _CONDITION_PARTICLES:
            if head is not None and head not in known and head not in _BOUND_NOUNS:
                return True
        elif not (tag.startswith("JK") or tag in ("NNB", "SN")):
            head = None  # the particle must attach to a noun phrase
    return False


def strip_keep_words(statement: str) -> str | None:
    """``statement`` without its "no change" words, or ``None`` if it has none.

    "300회를 그대로 유지하기로" -> "300회를 하기로": what is being kept, without
    the claim that it is being kept -- the part NLI misreads as contradicting
    an earlier *change* to that same value.
    """
    from kiwipiepy import Kiwi

    tokens = _kiwi(Kiwi).tokenize(statement)
    kept: list[tuple[str, str]] = []
    changed = False
    for i, token in enumerate(tokens):
        tag = token.tag.split("-")[0]
        if tag == "MAG" and token.form in _KEEP_ADVERBS:
            changed = True
            continue
        next_tag = tokens[i + 1].tag if i + 1 < len(tokens) else ""
        if tag == "NNG" and token.form == "유지" and next_tag.startswith("XSV"):
            changed = True
            continue
        if tag == "XSV" and i > 0 and tokens[i - 1].form == "유지":
            tag = "VV"  # 유지하다 -> 하다
        kept.append((token.form, tag))
    return _kiwi(Kiwi).join(kept) if changed else None


def _is_content(tag: str) -> bool:
    return tag in ("NNG", "NNP", "SL")


def _morphemes(text: str) -> list[tuple[str, str]]:
    """(form, base tag), with a noun and its derivational suffix joined
    (백지 + 화 -> 백지화) so a derived noun matches as one word."""
    from kiwipiepy import Kiwi

    out: list[tuple[str, str]] = []
    for token in _kiwi(Kiwi).tokenize(text):
        tag = token.tag.split("-")[0]
        if tag == "XSN" and out and out[-1][1].startswith("NN"):
            out[-1] = (out[-1][0] + token.form, out[-1][1])
            continue
        out.append((token.form, tag))
    return out


_KIWI_CACHE: list[Any] = []


def _kiwi(kiwi_cls: type) -> Any:
    if not _KIWI_CACHE:
        _KIWI_CACHE.append(kiwi_cls())
    return _KIWI_CACHE[0]
