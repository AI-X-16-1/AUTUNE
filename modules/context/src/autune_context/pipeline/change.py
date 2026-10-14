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

- **A withdrawal is often said by leaving or going back** (``marks_withdrawal``):
  "야간 운영은 이번 시즌으로 끝냅시다", "그 행사에서는 빠지죠", "계약을 끊는다",
  "손 뗍시다", "그만한다", "종료한다", "종이 영수증으로 되돌린다". No negation, no
  cancellation noun, and NLI often reads it as *entailed* by the decision it takes
  back, because the subject is the same. So it is read first, before NLI, and only
  where it cannot be something else: the verb heads the statement's last clause
  (not "화요일은 빼고 매일 연다", a part left out), takes no value ("8시에 닫는다",
  "세 명을 뺀다" move a parameter), was not already the earlier decision's word
  (after "야간에 닫는다", "계속 닫는다" restates it), is not said as a keep, and --
  for 끝내다 and 종료하다 -- has no 까지 deadline in its clause ("이번 달까지
  끝낸다" carries the decision out). The version's own statement and the utterance
  read in its place both count. Bringing in-house what an earlier decision gave out
  ("외부 업체에 맡긴다" -> "우리가 직접 한다", ``marks_in_house``) is read the same
  way, unless the later statement is about other work ("검수는 우리가 직접 한다"),
  and so is going back to how
  things were (예전처럼, 원래대로, ``marks_as_before``) -- but that one only where NLI
  contradicts, since the same words restate a decision NLI agrees with.

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
- The withdrawal cue misses an alternative said with an ordinary verb: "사람이 처음부터
  번역한다" after machine translation, "각자 받는다" after a shared terminal, "직접
  산다" after borrowing from a named supplier (no outsourcing word before it). It
  misses an in-house move said as a place ("병원 안에서 한다"). And it leaves out
  정리하다 and 마무리하다, though "그 서비스는 정리하죠" withdraws one: "그렇게
  정리했다" and "논의를 마무리했다" are how a decision is settled, far more often.
- The withdrawal cue outranks NLI, so where it misreads, NLI cannot correct it. A
  piece of work ended at a period with 로 ("설문은 이번 주로 종료한다") reads as a
  withdrawal, though it may be the plan carried out; only a 까지 deadline is told
  apart. A leaving verb in another sense reads as one too: "처리한 이슈는 닫는다",
  "기차표는 총무팀이 끊는다", "세금은 미리 뗀다", "장비는 다음 날 반납한다". Such a
  statement joins the earlier decision's thread only if it is about the same
  subject, which these rarely are.
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
# The past among them: going back to it, against a decision that changed it, is a return.
_BEFORE_NOUNS = {"원래", "예전", "종전", "이전"}
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
_SWAP_VERBS = {"갈아타", "바꾸", "옮기", "맡기", "넘기", "돌리"}  # tag VV
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
# Predicates that take a decision back without a negation or a cancellation noun:
# leaving (끝내다, 닫다, 빼다, 빠지다, 끊다, 손 떼다, 내려놓다, 그만하다) and going back
# (돌아가다, 되돌리다). Read only as the head of the statement's last clause and with
# no value as their argument (``marks_withdrawal``).
_EXIT_VERBS = {"끝내", "닫", "빼", "빠지", "끊", "떼", "내려놓", "돌아가", "되돌리", "되돌아가"}
_EXIT_NOUNS = {
    "종료", "해지", "폐지", "폐선", "폐쇄", "폐업", "철수", "해산", "탈퇴", "반납",
    "단종", "퇴출", "복귀", "원상복구", "환원",
}  # fmt: skip  # + 하다. Not 정리 or 마무리: "그렇게 정리했다" settles a decision.
_EXIT_ROOTS = {"그만"}  # XR + 하다: 그만하다
_END_PARTICLES = {"로", "으로", "까지"}
# Bringing in-house what an earlier decision gave out: the earlier statement hands
# the work to someone outside, the later one has it done directly or by our own.
_OUTSOURCING = {"위탁", "외주", "외부", "업체", "대행", "용역", "협력", "납품", "임대", "맡기"}
_IN_HOUSE = {"직접", "자체", "사내", "내부", "원내", "직영", "자사"}
# Leaving words that also mean finishing a piece of work: with a 까지 deadline in
# their clause ("이번 달까지 끝낸다") they carry the decision out, not take it back.
_FINISH_WORDS = {"끝내", "종료"}
# A decision about an owner: replacing the owner is a moved parameter, not a swap.
_OWNER_ROLES = {"담당", "책임", "주관", "리드", "오너", "맡", "담당자", "책임자", "주관자", "리더"}
_OWNER_TAGS = {"NNG", "NNP", "VV"}


def classify_change(
    forward: NliScores,
    backward: NliScores,
    current: str,
    previous: str,
    kept_forward: NliScores | None = None,
    *,
    statement: str | None = None,
) -> tuple[ChangeType, float]:
    """``forward`` scores (previous -> current), ``backward`` (current ->
    previous), ``kept_forward`` (previous -> ``strip_keep_words(current)``,
    when ``current`` had any). ``statement``: the version's own statement when
    ``current`` is a source utterance read in its place; a withdrawal said in
    either is one. Returns the change type and the NLI score that decided it."""
    said = {current, statement or current}
    if any(marks_withdrawal(text, previous) or marks_in_house(text, previous) for text in said):
        # NLI reads "야간 스키는 이번 시즌으로 끝냅시다" as entailed by "야간 스키를
        # 운영한다" -- the subject is the same -- so a leaving verb outranks it.
        return ChangeType.REVERSED, max(forward.contradiction, backward.contradiction)
    entailment = max(forward.entailment, backward.entailment)
    if entailment >= _ENTAILMENT_MIN:
        if forward.entailment < _ENTAILMENT_MIN and adds_condition(current, previous):
            return ChangeType.MODIFIED, backward.entailment
        return ChangeType.UNCHANGED, entailment
    if kept_forward is not None and kept_forward.entailment >= _ENTAILMENT_MIN:
        return ChangeType.UNCHANGED, kept_forward.entailment
    contradiction = max(forward.contradiction, backward.contradiction)
    if contradiction >= _CONTRADICTION_MIN:
        reversed_ = (
            marks_reversal(current, previous)
            or marks_replacement(current, previous)
            or marks_as_before(current)
        )
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


def marks_withdrawal(statement: str, previous: str = "") -> bool:
    """Whether ``statement`` takes something back by leaving it or going back from
    it: "야간 스키는 이번 시즌으로 끝냅시다", "셔틀버스는 계획에서 뺍시다", "그만한다",
    "N7 노선은 폐선합시다", "비닐 봉투로 되돌린다".

    Only the head of the last clause counts, so "일요일은 빼고 토요일에만 한다"
    (a part left out, the decision kept) and "정리해서 만 5천 원 받죠" do not; and
    not when what the predicate takes is a value -- "9시에 닫는다", "네 명을
    뺀다" move a parameter (``_takes_value``).

    Nor when ``previous`` already said that word -- after "주말 야간은 문을
    닫는다", "주말 야간은 계속 닫는다" restates the closing -- or when the
    statement says it keeps something (``marks_keep``)."""
    morphemes = _morphemes(statement)
    said_before = {form for form, _ in _morphemes(previous)} if previous else set()
    return not marks_keep(statement, previous) and any(
        _is_exit_predicate(morphemes, index)
        and _exit_word(morphemes, index) not in said_before
        and _ends_statement(morphemes, index)
        and not _takes_value(morphemes, index)
        and not _by_deadline(morphemes, index)
        for index in range(len(morphemes))
    )


def _by_deadline(morphemes: list[tuple[str, str]], head: int) -> bool:
    """Whether the predicate at ``head`` finishes a piece of work by a deadline:
    "이번 달까지 끝낸다", "다음 주까지 종료하기로 했다" carry the decision out.
    Only the finishing words (``_FINISH_WORDS``), and only a 까지 in the same
    clause: in "이번 시즌까지만 하고 끝냅시다" it belongs to 하고."""
    if _exit_word(morphemes, head) not in _FINISH_WORDS:
        return False
    start = head - 1 if morphemes[head][1] == "VV" else head - 2
    for index in range(start, -1, -1):
        form, tag = morphemes[index]
        if tag.startswith("E"):
            return False
        if form == "까지" and tag in ("JX", "JKB"):
            return True
    return False


def _exit_word(morphemes: list[tuple[str, str]], index: int) -> str:
    """The word that carries the exit at ``index``: the verb, or the noun before 하다."""
    form, tag = morphemes[index]
    return form if tag == "VV" else morphemes[index - 1][0]


def _is_exit_predicate(morphemes: list[tuple[str, str]], index: int) -> bool:
    """A leaving or going-back verb (빼다, 돌아가다), or the 하다 of such a noun or
    root (종료하다, 그만하다)."""
    form, tag = morphemes[index]
    if tag == "VV":
        return form in _EXIT_VERBS
    return (
        tag.startswith(("XSV", "XSA"))
        and index > 0
        and morphemes[index - 1][0] in (_EXIT_NOUNS | _EXIT_ROOTS)
    )


def _ends_statement(morphemes: list[tuple[str, str]], head: int) -> bool:
    """Whether the predicate at ``head`` heads the statement's last clause: its
    first ending closes the sentence (뺍시다), or nominalises it into the decision
    frame (빼기로 했다, 빠지는 게 낫다). A connective (빼고 …, 빼지 않는다) hands
    the clause on."""
    for index in range(head + 1, len(morphemes)):
        form, tag = morphemes[index]
        if tag in ("EP", "VX"):
            continue
        if tag == "EF" or tag == "ETN":
            return True
        if tag == "ETM":
            after = morphemes[index + 1][0] if index + 1 < len(morphemes) else ""
            return after in ("것", "거", "걸")
        return False
    return False


def _takes_value(morphemes: list[tuple[str, str]], head: int) -> bool:
    """Whether a phrase before ``head`` that the predicate takes -- marked as an
    object or with 에/로/까지/부터, not a topic -- is a quantity or a time: the
    "9시" of "9시에 닫는다", the "네 명" of "네 명을 뺀다". "24시간 운영은
    종료한다" names its subject with a number and still withdraws it."""
    phrase: list[tuple[str, str]] = []
    for form, tag in morphemes[:head]:
        if tag in _PHRASE_TAGS:
            phrase.append((form, tag))
            continue
        if phrase and (tag == "JKO" or (tag == "JKB" and form == "에")) and _is_value(phrase):
            return True
        if phrase and tag in ("JKB", "JX") and form in _END_PARTICLES and _is_quantity(phrase):
            return True
        phrase = []
    return False


def _is_quantity(phrase: list[tuple[str, str]]) -> bool:
    """A number, or a counter after one: "다섯 개", "9시". A period on its own --
    "이번 분기로 끝낸다" -- is when the decision ends, not a value it moves to."""
    return any(tag in ("SN", "MM", "NNB") for _, tag in phrase)


def marks_as_before(statement: str) -> bool:
    """Whether ``statement`` goes back to how things were before: 예전처럼,
    원래대로, 이전처럼. Read only where NLI finds it contradicting the earlier
    decision: what was before that decision is what it changed, so going back
    takes it back. Where NLI does not, the same words say nothing changed
    (``marks_keep``). 지금대로 and 현행처럼 are the present, never a return."""
    morphemes = _morphemes(statement)
    return any(
        tag.startswith("NN") and form in _BEFORE_NOUNS and after[0] in _AS_PARTICLES
        for (form, tag), after in zip(morphemes, [*morphemes[1:], ("", "")], strict=True)
    )


def marks_in_house(current: str, previous: str) -> bool:
    """Whether ``current`` brings in-house what ``previous`` gave out: "장비 대여는
    렌탈 업체에 위탁한다" -> "리조트가 직접 한다". An alternative that excludes the
    earlier one, said with neither a swap verb nor a negation.

    Only when ``current`` is about that same work: a topic or an object it names
    must be one ``previous`` names too. After "번역은 외주로 돌린다", "검수는
    우리가 직접 한다" keeps the outsourcing and adds a step of its own."""
    before = {form for form, _ in _morphemes(previous)}
    if not before & _OUTSOURCING:
        return False
    now = _morphemes(current)
    if any(head not in before for head in _topic_heads(now)):
        return False  # "검수는 우리가 직접 한다": other work than what was given out
    return any(form in _IN_HOUSE for form, tag in now if tag in ("MAG", "NNG"))


def _topic_heads(morphemes: list[tuple[str, str]]) -> list[str]:
    """The nouns a statement is about: each one right before a topic particle
    (은/는), and each one right before an object particle (을/를) in its last
    clause. "업체 평가는 …" -> 평가, "… 일정을 잡는다" -> 일정; but not the 장비 of
    "장비를 들여서 원내에서 한다", the means of an earlier clause."""
    last_clause = max(
        (index + 1 for index, (_, tag) in enumerate(morphemes) if tag == "EC"), default=0
    )
    return [
        form
        for index, ((form, tag), (particle, particle_tag)) in enumerate(
            zip(morphemes, morphemes[1:], strict=False)
        )
        if tag in ("NNG", "NNP")
        and (particle in ("은", "는") or (particle_tag == "JKO" and index >= last_clause))
    ]


def marks_keep(statement: str, previous: str = "") -> bool:
    """Whether ``statement`` says something stays as it is: a keep adverb
    (그대로, 계속), a keep noun (유지, 동결), "as now / as before" (지금대로,
    기존처럼), "no change" (변동 없이), or a change verb said with a negation
    (바꾸지 않는다, 안 고친다) -- but not a change with the direction ``previous``
    moved in, negated or absent (``_negated_changes``; "인상 없이" after a raise)."""
    morphemes = _morphemes(statement)
    moved = _directions_moved(previous)
    if _negated_changes(morphemes, moved):
        return True
    for index, (form, tag) in enumerate(morphemes):
        after = morphemes[index + 1] if index + 1 < len(morphemes) else ("", "")
        if tag == "MAG" and form in _KEEP_ADVERBS and not _yields(morphemes, index):
            return True
        if tag.startswith("NN") and form in _KEEP_NOUNS and not after[1].startswith("NN"):
            return True  # 현행 유지, 유지하다 -- not 유지보수, a compound
        if tag.startswith("NN") and form in _AS_BEFORE_NOUNS and after[0] in _AS_PARTICLES:
            return True
        if tag.startswith("NN") and form in _CHANGE_NOUNS and _DIRECTIONS.get(form) not in moved:
            # 변동 없이, 변경이 없다 -- but "인상 없이" after a raise takes the raise back
            rest = morphemes[index + 1 : index + 3]
            if any((f, t) in _ABSENCE_TOKENS for f, t in rest):
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
