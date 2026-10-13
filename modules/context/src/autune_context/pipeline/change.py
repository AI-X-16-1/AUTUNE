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

- **A keep that NLI cannot place is still a keep.** "10시 마감은 그대로 둡시다"
  against "매장 영업은 밤 10시까지 한다" scores neutral both ways, and neutral
  used to read as a moved parameter. A statement that says nothing changes
  (``marks_keep``: 그대로, 지금대로, 유지, 동결, 변동 없이, a change verb said with
  a negation -- 바꾸지 않는다, 안 고친다) and that NLI does not find contradicting
  the earlier one is a restatement. A negated change verb is also not a
  withdrawal: "바꾸지 않기로" keeps the decision, so ``marks_reversal`` skips it.
  A verb with a direction (올리, 늘리, 인상; 내리, 줄이, 인하) is the exception
  when the earlier decision moved that same way: after "가격을 10% 올린다",
  "가격은 올리지 않기로 했다" withdraws the raise, so it is a withdrawal and no
  keep. A keep word inside a compound noun (유지보수) or in a clause that gives way
  to another (계속 논의하다가 …) says nothing about the decision.

- **A replacement carries none of those words.** "MySQL에서 PostgreSQL로 옮기기로
  했다" withdraws the earlier choice as surely as "MySQL은 쓰지 않기로 했다" does,
  but has no negation or cancellation to fire ``marks_reversal`` -- so a swap of one
  thing for another read as a moved parameter. ``marks_replacement`` reads it
  structurally instead: a swap verb (전환, 교체, 대체, 이전, 갈아타, 바꾸, 옮기, ...)
  whose ``(으)로``/``에`` target is a *new* thing. It stays quiet when the target is a
  number, a weekday or time of day, or a unit ("월 단위로"), because those are a
  parameter moving; and when the decision is about an owner (담당, 책임, 주관, ...),
  because a replaced owner is a moved parameter too (see below).

The cue lists are general Korean negation, cancellation, restriction and
replacement vocabulary, not words lifted from the evaluation set. One kind of
negation is not a cue: "차질 없이", "문제없이", "예외 없이" say how a decision is carried
out, not that it was withdrawn, so an absence whose head is a hitch-or-exception
noun is skipped. Known limits of a lexical cue:

- A modification phrased with a negation ("주 1회로 줄이고 월요일은 하지
  않는다") reads as reversed.
- "대신" marks a replacement, and a replaced *owner* is a moved parameter:
  "김민경 대신 강민구가 맡기로" reads as reversed, not modified. Telling a
  person from a vendor or a technology by vocabulary alone is not reliable --
  kiwipiepy splits names unpredictably ("김민/NNP 경/NNG"), and company names
  are proper nouns too -- so this stays a limit rather than a rule.
- The replacement cue cannot tell a *swap* from a *state change* said with the
  same verb: "베타를 무료에서 유료로 전환한다" reads as a replacement, though it is
  arguably a pricing parameter. It also misses a replacement said without a swap
  verb ("사내 인력이 직접 운영한다" after "외주로 운영한다"), and an owner it
  cannot recognise by role word ("A팀이 맡던 일을 B팀으로 넘긴다" has no 담당).
  The target of "…하는 걸로 바꿔요" is read as a thing when kiwipiepy tags 걸 as a
  noun, though it is the nominaliser 것 + 으로 -- a very common way to say a decision
  aloud. And the cancellation noun in "예약 취소 기한을 5일 전으로 늘려요" still reads as
  a cancellation, which is ``marks_reversal`` and older than this cue.
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
# Nouns whose absence says "smoothly", not "withdrawn": 차질 없이, 문제없이,
# 예외 없이, (추가) 비용·예산 없이, 차질이 없도록. Checked against the noun an
# absence (없이, 없-) attaches to, skipping a subject particle in between.
# 중단 is left out on purpose: it is itself a reversal noun and matches first.
_SMOOTH_NOUNS = {
    "차질",
    "문제",
    "예외",
    "지연",
    "비용",
    "예산",
    "부담",
    "사고",
    "탈",
    "무리",
    "오류",
    "장애",
    "이견",
    "누락",
    "변동",
    "변경",
    "제한",
}
_ABSENCE_TOKENS = {("없이", "MAG"), ("없", "VA")}
# Particles that narrow (만), add (도) or extend (까지) what a decision covers.
_CONDITION_PARTICLES = {"만", "도", "까지"}
# Bound expressions restate a threshold ("16 이상만" == "최소 16"), not add one.
_BOUND_NOUNS = {"이상", "이하", "미만", "초과"}
# Adverbs that say "no change from now" -- stripped before re-asking NLI.
_KEEP_ADVERBS = {"계속", "계속해서", "그대로", "여전히", "변함없이"}
# Nouns that say "kept": 유지하다, 동결하다, 현행 유지.
_KEEP_NOUNS = {"유지", "동결", "존치"}
# "As it is now / was": 지금대로, 원래대로, 기존처럼. A noun of the present or the
# past followed by 대로 or 처럼.
_AS_BEFORE_NOUNS = {"지금", "현재", "원래", "예전", "기존", "종전", "현행", "이전", "평소"}
_AS_PARTICLES = {"대로", "처럼"}
# Verbs that change something. Negated (바꾸지 않다, 안 고치다) they say "kept".
_CHANGE_VERBS = {"바꾸", "고치", "손대", "손보", "건드리", "뒤집", "늘리", "줄이", "올리", "내리"}
_CHANGE_NOUNS = {"변경", "수정", "조정", "변동", "변화", "인상", "인하"}  # + 하다 / + 없다
# Change words with a direction. Negated after an earlier decision that moved the
# same way ("10% 올린다" -> "올리지 않기로"), they withdraw it rather than keep it.
_DIRECTIONS = {
    "올리": "up",
    "늘리": "up",
    "인상": "up",
    "내리": "down",
    "줄이": "down",
    "인하": "down",
}
# A clause ending that gives way to the next clause: "계속 논의하다가 …" -- what was
# kept is the discussion, not the decision.
_YIELDING_ENDINGS = {"다가"}
_NEGATING_AUX = {("않", "VX"), ("말", "VX")}
_NEGATING_ADVERBS = {("안", "MAG"), ("못", "MAG")}

# Verbs that swap one thing for another. ``바꾸``/``옮기``/``이전`` also move a
# parameter ("목요일로 옮긴다"); what tells the two apart is the target, not the verb.
_SWAP_VERBS = {"갈아타", "바꾸", "옮기", "맡기"}  # tag VV
_SWAP_NOUNS = {"전환", "교체", "대체", "이관", "이전", "통일"}  # noun + 하다
_TARGET_PARTICLES = {"로", "으로", "에"}
# What a noun phrase may be made of and still be one target.
_PHRASE_TAGS = {"NNG", "NNP", "NNB", "SL", "SN", "SH", "MM", "MAG", "XR"}
# A target made of these is a value, not a thing: a parameter is moving.
_QUANTITY_OR_TIME = {
    "월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일",
    "주말", "평일", "오전", "오후", "아침", "점심", "저녁", "새벽", "야간",
    "분기", "상반기", "하반기", "연초", "연말", "월초", "월말", "내일", "모레",
    "매일", "매주", "매월", "매달", "격주", "단위", "이상", "이하", "미만", "초과",
    "이내", "정도", "월", "주", "일", "년", "분", "초", "시", "회", "건", "명", "개",
    "원", "배", "기간", "주기", "횟수",
}  # fmt: skip
# Latin unit abbreviations: a number touching one of these is a quantity (10GB), not a
# name (3PL). Lower-cased.
_LATIN_UNITS = {"kb", "mb", "gb", "tb", "pb", "ms", "mbps", "gbps", "hz", "khz", "ghz",
                "px", "dpi", "fps", "kg", "mg", "km", "cm", "mm", "k", "m", "s"}  # fmt: skip
# A decision about an owner: replacing the owner is a moved parameter, not a swap.
_OWNER_ROLES = {"담당", "책임", "주관", "리드", "오너", "맡", "담당자", "책임자", "주관자", "리더"}
_OWNER_TAGS = {"NNG", "NNP", "VV"}


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
        reversed_ = marks_reversal(current, previous) or marks_replacement(current, previous)
        return (ChangeType.REVERSED if reversed_ else ChangeType.MODIFIED), contradiction
    # Neither entailed nor contradicted: NLI has no opinion, and a swap said without
    # a negation is exactly what it scores neutral as often as contradictory.
    if marks_replacement(current, previous):
        return ChangeType.REVERSED, forward.neutral
    if marks_keep(current, previous):
        return ChangeType.UNCHANGED, forward.neutral
    return ChangeType.MODIFIED, forward.neutral


def marks_reversal(statement: str, previous: str = "") -> bool:
    """Whether ``statement`` negates, stops, or cancels/replaces something.
    ``previous``: the earlier statement, which decides whether a negated verb with
    a direction keeps the decision or withdraws it (``_negated_changes``)."""
    if "더 이상" in statement:
        return True
    head: str | None = None  # the noun an absence would attach to
    morphemes = _morphemes(statement)
    kept = _negated_changes(morphemes, _directions_moved(previous))
    for index, (form, tag) in enumerate(morphemes):
        if index in kept:  # 바꾸지 않다: the decision is kept, not withdrawn
            head = None
            continue
        if (form, tag) in _ABSENCE_TOKENS and head in _SMOOTH_NOUNS:
            head = None
            continue
        if (form, tag) in _REVERSAL_TOKENS:
            return True
        if tag.startswith("NN") and form in _REVERSAL_NOUNS:
            return True
        if tag.startswith("NN"):
            head = form
        elif tag != "JKS":  # 차질이 없도록: the particle keeps the head
            head = None
    return False


def marks_keep(statement: str, previous: str = "") -> bool:
    """Whether ``statement`` says something stays as it is: a keep adverb
    (그대로, 계속), a keep noun (유지, 동결), "as now / as before" (지금대로,
    기존처럼), "no change" (변동 없이), or a change verb said with a negation
    (바꾸지 않는다, 안 고친다) -- but not a verb with the direction ``previous``
    moved in (``_negated_changes``)."""
    morphemes = _morphemes(statement)
    if _negated_changes(morphemes, _directions_moved(previous)):
        return True
    for index, (form, tag) in enumerate(morphemes):
        after = morphemes[index + 1] if index + 1 < len(morphemes) else ("", "")
        if tag == "MAG" and form in _KEEP_ADVERBS and not _yields(morphemes, index):
            return True
        if tag.startswith("NN") and form in _KEEP_NOUNS and not after[1].startswith("NN"):
            return True  # 현행 유지, 유지하다 -- not 유지보수, a compound
        if tag.startswith("NN") and form in _AS_BEFORE_NOUNS and after[0] in _AS_PARTICLES:
            return True
        if tag.startswith("NN") and form in _CHANGE_NOUNS:
            rest = morphemes[index + 1 : index + 3]
            if any((f, t) in _ABSENCE_TOKENS for f, t in rest):  # 변동 없이, 변경이 없다
                return True
    return False


def _negated_changes(
    morphemes: list[tuple[str, str]], withdrawn: frozenset[str] = frozenset()
) -> set[int]:
    """Indices of the negating morphemes (않, 말, 안, 못) whose predicate is a
    change verb: 바꾸지 않다, 변경하지 말다, 안 바꾸다. A predicate whose direction
    is in ``withdrawn`` is left out: "올리지 않다" after a raise takes it back."""
    return {
        negator
        for negator, predicate in _negated_predicates(morphemes).items()
        if _direction(morphemes, predicate) not in withdrawn
    }


def _negated_predicates(morphemes: list[tuple[str, str]]) -> dict[int, int]:
    """Negating morpheme index -> the index of the change predicate it negates."""
    out: dict[int, int] = {}
    for index, (form, tag) in enumerate(morphemes):
        if (form, tag) in _NEGATING_AUX:
            # -지 않다: the predicate sits before the connective 지.
            j = index - 1
            if j >= 0 and morphemes[j] == ("지", "EC"):
                j -= 1
            if j >= 0 and _is_change_predicate(morphemes, j):
                out[index] = j
        elif (form, tag) in _NEGATING_ADVERBS:
            if index + 1 < len(morphemes) and _is_change_predicate(morphemes, index + 1):
                out[index] = index + 1
    return out


def _direction(morphemes: list[tuple[str, str]], index: int) -> str | None:
    """The direction of the change word at ``index``, or of the noun before its 하다
    (인상하다), or ``None`` for a change with none (바꾸다, 변경하다)."""
    form, tag = morphemes[index]
    if tag.startswith("XSV") and index > 0:
        form = morphemes[index - 1][0]
    return _DIRECTIONS.get(form)


def _directions_moved(statement: str) -> frozenset[str]:
    """The directions ``statement`` moves something in, said without a negation:
    "가격을 10% 올린다" -> up, "인상" -> up; "올리지 않는다" -> none."""
    if not statement:
        return frozenset()
    morphemes = _morphemes(statement)
    negated: set[int] = set()
    for predicate in _negated_predicates(morphemes).values():
        negated.add(predicate)
        if morphemes[predicate][1].startswith("XSV"):
            negated.add(predicate - 1)  # the 인상 of 인상하지 않다
    return frozenset(
        direction
        for index, (form, tag) in enumerate(morphemes)
        if index not in negated
        and (tag == "VV" or tag.startswith("NN"))
        and (direction := _DIRECTIONS.get(form)) is not None
    )


def _yields(morphemes: list[tuple[str, str]], index: int) -> bool:
    """Whether the clause the adverb at ``index`` opens gives way to another
    ("계속 논의하다가 …"): its first predicate ends in a yielding ending."""
    for later in range(index + 1, len(morphemes)):
        tag = morphemes[later][1]
        if tag == "EC":
            return morphemes[later][0] in _YIELDING_ENDINGS
        if tag in ("EF", "ETN", "ETM", "SF"):
            return False
    return False


def _is_change_predicate(morphemes: list[tuple[str, str]], index: int) -> bool:
    """Whether the morpheme at ``index`` is a change verb, or the 하다 of a
    change noun (변경하다)."""
    form, tag = morphemes[index]
    if tag == "VV" and form in _CHANGE_VERBS:
        return True
    if tag.startswith("NN") and form in _CHANGE_NOUNS:
        return index + 1 < len(morphemes) and morphemes[index + 1][1].startswith("XSV")
    if tag.startswith("XSV") and index > 0:
        prev_form, prev_tag = morphemes[index - 1]
        return prev_tag.startswith("NN") and prev_form in _CHANGE_NOUNS
    return False


def marks_replacement(current: str, previous: str) -> bool:
    """Whether ``current`` swaps something in ``previous`` for a different thing.

    A swap verb whose ``(으)로``/``에`` target is new -- not already in ``previous`` --
    and is neither a quantity or time (a parameter moving) nor said of an owner (a
    replaced owner is a moved parameter, ``dataset.py``). Structural, not a
    vocabulary of what gets swapped: the verbs say *that* something is replaced and
    the target says it is a thing rather than a value.
    """
    now = _morphemes(current)
    before = _morphemes(previous)
    if any(form in _OWNER_ROLES for form, tag in (*now, *before) if tag in _OWNER_TAGS):
        return False
    known = {form for form, tag in before if _is_content(tag)}
    for index, (form, tag) in enumerate(now):
        if not _is_swap_verb(now, index, form, tag):
            continue
        target = _swap_target(now, index)
        if target is None:
            continue
        content = [f for f, t in target if _is_content(t)]
        if content and not _is_value(target) and not all(f in known for f in content):
            return True
    return False


def _is_value(phrase: list[tuple[str, str]]) -> bool:
    """Whether a target phrase is a quantity or a time rather than a thing."""
    for index, (form, tag) in enumerate(phrase):
        if form in _QUANTITY_OR_TIME or (tag == "SN" and not _is_name_part(phrase, index)):
            return True
    return False


def _is_name_part(phrase: list[tuple[str, str]], index: int) -> bool:
    """A digit run that is part of a name: Latin letters before it (S3, B2B, K8s), or
    after it unless they are a unit (3PL, 5G are names; 10GB, 200ms are quantities).
    A unit follows its number, so a letter *before* one is never a unit."""
    if index > 0 and phrase[index - 1][1] == "SL":
        return True
    after = phrase[index + 1] if index + 1 < len(phrase) else None
    return after is not None and after[1] == "SL" and after[0].lower() not in _LATIN_UNITS


def _is_swap_verb(morphemes: list[tuple[str, str]], index: int, form: str, tag: str) -> bool:
    if tag == "VV":
        return form in _SWAP_VERBS
    # ``전환`` is a swap only as ``전환하다``: bare, ``이전 방식`` is "the former way".
    return (
        tag.startswith("NN")
        and form in _SWAP_NOUNS
        and index + 1 < len(morphemes)
        and morphemes[index + 1][1].startswith("XSV")
    )


def _swap_target(morphemes: list[tuple[str, str]], verb: int) -> list[tuple[str, str]] | None:
    """The noun phrase marked ``(으)로`` or ``에`` before the verb at ``verb``, or
    ``None`` -- a verb with no such phrase, or another clause's particle in the way,
    names nothing it is swapped *to*."""
    for index in range(verb - 1, -1, -1):
        form, tag = morphemes[index]
        if tag == "JKB" and form in _TARGET_PARTICLES:
            phrase: list[tuple[str, str]] = []
            for earlier in range(index - 1, -1, -1):
                if morphemes[earlier][1] not in _PHRASE_TAGS:
                    break
                phrase.append(morphemes[earlier])
            return phrase[::-1] or None
        if tag.startswith(("J", "V", "E", "S")) and tag != "SN":
            return None
    return None


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
