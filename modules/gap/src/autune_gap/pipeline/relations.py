"""Step 2: what joins two topics, read off the marker the speaker said.

Pure functions and one class that wires them together. No model, no weights, no
network — the same reason ``spoken`` is pure: these are judgements about what
the graph asserts, and a judgement nobody can exercise without loading a model
is a judgement nobody tests.

**Why rules first.** ``docs/modules/gap.md`` step 2 plans LLM assistance for the
hard cases, and issue #32 sets the non-LLM share at about 70%. Rules are what
that 70% is, so they are written and measured before anything is sent anywhere.
What they cannot do is then a list of named cases rather than a shrug.

**Why so few rules.** C's metric is precision. A relation the meeting did not
assert is worse than no relation at all: risk scoring (#35) reads ``blocked_by``
as a finding, and a rule that guesses puts a blocker in the report that nobody
said out loud.

Every rule keys on a **marker**, a surface string the speaker used, and fires
only when two topics sit around it in the same utterance. Nothing here infers a
relation from proximity alone — proximity is ``co_occurs``, which ``graph``
writes without asking this module.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .base import Entity, Relation

MAX_MARKER_DISTANCE = 40
"""How far, in characters, a marker may sit from the mention it binds.

A long utterance holds several clauses, and a marker in the third one says
nothing about the topics in the first. The cap is what keeps "A ... (thirty
characters of something else) ... B가 필요합니다" from asserting that A depends
on B. Forty characters is roughly one spoken Korean clause; it is a guess, and
it is the first number to move when dismissals (#35) say the rules over-fire.
"""


_WORD_CHARACTER = re.compile(r"[0-9A-Za-z가-힣]")
"""What a mention may not begin immediately after.

``실시간`` sits inside ``비실시간``, and without this guard "비실시간 처리가
필요해서 검색 기능은 미뤘습니다" asserted that 검색 기능 depends on 실시간 — a
topic whose name the utterance contains and whose meaning it negates.

**Only the left side is guarded.** Korean attaches particles directly to the
noun, so the character after a mention is grammar more often than not: 실시간은,
실시간이, 실시간으로. Refusing those would refuse every mention in a sentence
that says anything about it. What sits on the right and is *not* a particle —
실시간성 — needs the tagger to tell apart, which is why ``noun_terms`` runs
there and this does not. Raised in review of #249.
"""


@dataclass(frozen=True)
class Mention:
    """Where one topic was named inside one utterance."""

    text: str
    start: int
    end: int


_CAUSAL = ("어서", "아서", "라서", "때문", "탓에", "으로 인해")
"""Connectives that make the clause before them the reason for what follows.

``blocked_by`` needs one. Without it "콜드스타트 처리가 안 잡혀 있습니다" is a
status report, and reading it as the reason 실시간 cannot proceed is the rule
inventing a link between two things the speaker kept apart.

**They do not all sit in the same place.** ``어서``/``아서``/``라서`` are verb
endings and attach to the predicate itself — "안 잡혀 있**어서**" — so they
follow the blocker word. ``때문``/``탓에``/``으로 인해`` are nouns and phrases
that head the reason, and Korean puts the reason before what it explains —
"캐시 **때문에** 막혀" — so they precede it. A rule that looked only forwards
from the cue read the first three and none of the last three, and every
relation a meeting stated the second way was dropped. #254.
"""

_CAUSAL_BEFORE = ("때문", "탓에", "으로 인해")
"""The three of those six that may be read *backwards* from the cue.

The nouns and phrases, and only them. ``어서``/``아서``/``라서`` are verb endings
that close the clause they sit in, and ``_CLAUSE_BREAKS`` has no entry for them
— nothing stops ``_clause_before`` from reading straight past one. Searching for
all six behind the cue therefore paired a blocker word with the previous
clause's reason wherever no other boundary happened to sit between them:

    결제 모듈은 시간이 없어서 로그인 모듈 이슈는 못 봤습니다
        -> 로그인 모듈 blocked_by 결제 모듈

which is a finding nobody stated, in the one relation the report treats as a
finding on its own. Restricting the backward window to the three that head a
reason is what makes the asymmetry this rule is built on hold in the code as
well as in the docstring above.
"""

_BLOCKERS = (
    "안 잡",
    "안 되",
    "안돼",
    "못 하",
    "못하",
    "미정",
    "막혀",
    "막히",
    "무리",
    "이슈",
    "리스크",
    "블로커",
)
"""What a speaker says when something is in the way.

Surface strings, and spoken ones: a meeting says "안 잡혀 있어서", not
"미해결 상태로 인하여". They are matched literally rather than by morpheme tag,
because the tag of 안 잡혀 is a verb phrase like any other — what makes it a
blocker is the word, not the grammar.
"""

_RESOLVED = ("해결", "해소", "풀렸", "풀려", "정리되", "정리했", "처리")
"""What a speaker says when the thing in the way is gone.

A blocker word in a causal clause is a relation only while the blocker stands.
"캐시 이슈가 해결되어서 검색 기능은 바로 진행합니다" carries 이슈 and a causal
어서 and asserts the reverse of a blocker — the same shape as "이슈는 없어서",
which is what ``_NEGATIONS`` already catches, except that this one is phrased
positively. Both put a ``blocked_by`` in the report that says the opposite of
what the meeting said, and ``blocked_by`` is the relation C treats as a finding
in its own right. Raised in review of #249 by @kjfcvx12.

Read together with ``_UNDONE``: 해결 and 처리 are the words for doing the thing
*and* for failing to. "이슈 처리가 안 돼서" and "해결이 안 되어서" are blockers,
and a list matched as bare substrings would have thrown both away.
"""

_UNDONE = ("안 ", "안되", "안돼", "못 ", "못하", "못했", "않")
"""What takes a resolution back, for ``_resolved``.

Wider than ``_NEGATIONS`` on purpose. 안 is excluded there because "캐시
없이는 안 됩니다" is a need stated through a negative; here there is no such
reading — a resolution word with 안 after it is a resolution that did not
happen, and nothing else.
"""

_NEEDS = ("필요", "있어야", "되어야", "돼야", "선행", "전제", "없이는", "없으면")
"""What a speaker says when one thing waits on another.

``없이는``/``없으면`` are here rather than in ``_BLOCKERS`` because they state a
condition, not a state: "인덱스 없으면 정렬 로직은 못 붙여요" says what the
meeting needs, and the graph can carry that without claiming anybody is blocked
today.
"""

_ALTERNATIVES = re.compile(r"대신|말고|보다는|아니라|반면|(?<![A-Za-z])(?:vs|versus)(?![A-Za-z])")
"""Markers that weigh one topic against another.

A pattern rather than a list of strings, because the Latin two need word
boundaries: "API devs 검색 기능" contains ``vs`` inside ``devs``, and a substring
match made the two topics alternatives to each other on the strength of a
plural. The Korean markers are matched as substrings — a particle attaches
directly and there is no boundary to anchor to. Raised in review of #249.

``는데`` and ``지만`` are **not** here, and leaving them out is the judgement
this rule set argued about most. Spoken Korean uses ``는데`` as plain sentence
glue: "실시간 개인화로 합의했는데 오늘은 인기순 정렬 얘기가 나왔네요" really is
a contrast, "자료 공유드리는데 확인 부탁드려요" is not, and nothing in the
surface string tells the two apart. A marker that fires on every second
utterance would make ``alternative_to`` the most common relation in the graph
and every one of them a coin flip. It is the clearest case for the LLM
assistance step 2 is promised — see ``docs/modules/gap.md``.
"""

_CLAUSE_BREAKS = ("고 ", "고요", "며 ", "지만", "는데", "니까", ".", ",")
"""Where one spoken clause ends and the next begins.

Every rule reads **one clause**. Korean puts a new subject after a connective
ending, so a marker binds what is in its own clause and says nothing about the
next one: in "정렬 로직은 인덱스가 필요하고 캐시는 다음 주에 봅시다" the 필요
belongs to 인덱스 and 캐시 is a different sentence wearing the same breath.
Without the boundary the rule read 캐시 as the thing that needs 인덱스.

Surface strings, and a short list. A boundary this misses costs a relation; one
it invents only shortens a window. Raised in review of #249.
"""

_NEGATIONS = ("없", "않")
"""What turns an assertion into its opposite, inside the same clause.

`필요 없습니다` and `필요하지 않아요` both carry the marker 필요 and say the
reverse of what it means. 안 is deliberately **not** here: "캐시 없이는 안
됩니다" is a need stated through a negative, and the cue 없이는 already carries
its own 없. Raised in review of #249.
"""

_REASON_DENIED = _NEGATIONS + ("아니",)
"""What takes back the reason a connective heads, read between it and the cue.

``_NEGATIONS`` guards what follows the *cue*; this guards what follows the
*connective*, and they are different windows. "캐시 때문이 아니라 그냥 막혀
있습니다" names a reason in order to deny it, and the backward path asserted it
anyway — 검색 기능 blocked_by 캐시, the exact reverse of the sentence, in the one
relation the report treats as a finding. Raised in review of #254 by @lsh2217.

``아니`` is not in ``_NEGATIONS`` and does not belong there: ``_ALTERNATIVES``
reads ``아니라`` as a contrast marker ("A가 아니라 B"), and a cue near one is not
thereby denied. Here the window is narrow enough that it means what it says.

The cost is 아니 as a spoken filler — "캐시 때문에, 아니 그러니까, 막혀" loses a
relation the speaker did state. Loss, which is the direction this module errs
in, and the window is the few characters between a connective and its cue.
"""

_QUESTIONS = ("나요", "가요", "까요", "?")
"""What turns an assertion into a question.

"인덱스가 필요한가요?" asks whether the dependency exists. The meeting has not
said that it does, and a graph that records the question as the answer is a
graph that reports a dependency nobody asserted — the shape of false finding C's
precision target exists to refuse. Raised in review of #249.
"""

_TOPIC_MARKERS = ("은", "는")
"""What Korean puts on the thing a sentence is about.

Used to pick the *source* end when the marker's own clause does not supply one.
The nearest preceding mention is the wrong default — in "정렬 로직은 인덱스에
의존하지 않고 캐시 없이는 안 됩니다" that is 인덱스, which sits inside the
clause the sentence just denied. The thing the sentence is about is 정렬 로직,
and it says so with 은. Raised in review of #249.
"""

# ``A의 B`` has no rule, and that is a decision rather than an omission.
#
# 의 marks possession and composition with the same character. "검색의 정렬
# 로직" is a part of a thing; "검색 기능의 담당자 일정" is somebody's calendar,
# and the rule read it as `담당자 일정 part_of 검색 기능`. Nothing in the
# surface string tells the two apart — the same argument that keeps ``는데`` out
# of ``_ALTERNATIVES``, and the same answer: it is a case for the assisted
# implementation, not for a marker list.
#
# ``part_of`` stays in ``base.RELATION_LABELS`` because the graph can carry it
# and #35 weights it; what is gone is the claim that a rule can read it.
# Raised in review of #249.


def mention_spans(text: str, mentions: Iterable[str]) -> list[Mention]:
    """Every place one of ``mentions`` was said in ``text``, in order.

    Matched whitespace-insensitively, because a mention does not always spell
    the way the utterance did: ``spoken.noun_terms`` joins a run with single
    spaces and ASR output carries double ones. "검색  개인화 기능" in the
    transcript is "검색 개인화 기능" as an entity, and a literal search would
    find neither the mention nor any relation it is in.

    Longer mentions are claimed first and a character belongs to at most one —
    the rule ``FakeNer`` and the noun-run pass both already follow. Without it
    "검색 기능" inside "검색 기능 개선" is a second mention over the same
    characters, and a rule keyed on distance would relate a topic to itself.

    A mention has to start a word (``_WORD_CHARACTER``): ``실시간`` inside
    ``비실시간`` is a different thing wearing the same characters.
    """
    claimed: list[Mention] = []
    # `(len, text)`, not `len`. Ties fell back to set iteration order, which
    # Python derives from the string hash and `PYTHONHASHSEED` randomises per
    # process: two equally long mentions overlapping in one utterance claimed
    # the span in whichever order that run happened to produce, so a worker
    # restart changed the graph a re-processed meeting came back with. This
    # function's own docstring says deterministic. Raised in review of #249 by
    # @lsh2217.
    for mention in sorted({m for m in mentions if m.strip()}, key=_claim_order, reverse=True):
        pattern = re.compile(r"\s+".join(re.escape(part) for part in mention.split()))
        for match in pattern.finditer(text):
            start, end = match.span()
            if start > 0 and _WORD_CHARACTER.match(text[start - 1]):
                continue
            if any(start < other.end and other.start < end for other in claimed):
                continue
            claimed.append(Mention(text=mention, start=start, end=end))
    return sorted(claimed, key=lambda mention: mention.start)


def _claim_order(mention: str) -> tuple[int, str]:
    """Longest first, and ties broken by the text itself so a re-run agrees."""
    return len(mention), mention


def relations_in(text: str, mentions: Sequence[Mention]) -> list[tuple[str, str, str]]:
    """``(source, target, relation)`` triples this utterance asserts.

    Each rule is a marker and a way of reading which mention is which end:

    - **``depends_on``** — a need word (``_NEEDS``) the speaker actually
      asserted: not negated, not asked (``_asserted``). The thing needed is the
      mention just before it, which is where both Korean word orders put it:
      "정렬 로직은 인덱스가 필요합니다" and "인덱스가 있어야 정렬 로직을
      붙입니다" both name 인덱스 immediately before the marker. The other end is
      read by ``_ends_around``.
    - **``blocked_by``** — a blocker word (``_BLOCKERS``) *offered as a reason*,
      so a causal connective (``_CAUSAL``) has to sit beside it **in the same
      clause**, on either side: 어서/아서/라서 attach to the predicate and
      follow the cue, 때문에/탓에/으로 인해 precede it. The same assertion guard
      applies: "캐시 이슈는 없어서" is a blocker that is not there. Ends are read
      the same way, from the connective when it is the one behind.
    - **``alternative_to``** — a contrast marker in the gap between two
      mentions, and nowhere else.

    There is no ``part_of`` rule. See the comment where one used to be.

    A pair may come back with more than one relation; ``gap_topic_edges`` is
    unique on ``(source, target, relation)`` and the read API orders by relation
    for exactly that case. A pair never comes back twice with the same relation,
    and a mention is never related to itself — the table forbids the self-loop
    and the rule has nothing to say anyway.
    """
    if len(mentions) < 2:
        return []

    found: list[tuple[str, str, str]] = []
    for marker, relation in _directed_markers(text):
        pair = _ends_around(text, mentions, marker)
        if pair is not None:
            found.append((pair[0], pair[1], relation))
    found.extend(_contrast_pairs(text, mentions))

    seen: set[tuple[str, str, str]] = set()
    unique: list[tuple[str, str, str]] = []
    for triple in found:
        if triple[0] == triple[1] or triple in seen:
            continue
        seen.add(triple)
        unique.append(triple)
    return unique


def _clause_after(text: str, index: int) -> str:
    """What is left of the clause that starts at ``index``.

    Capped at ``MAX_MARKER_DISTANCE`` as well, so a run-on sentence with no
    connective in it cannot make the window the whole utterance.
    """
    window = text[index : index + MAX_MARKER_DISTANCE]
    cut = len(window)
    for boundary in _CLAUSE_BREAKS:
        found = _find_boundary(window, boundary)
        if found != -1:
            cut = min(cut, found + len(boundary))
    return window[:cut]


def _clause_before(text: str, index: int) -> tuple[int, str]:
    """The tail of the clause that runs up to ``index``, and where it starts.

    The mirror of ``_clause_after`` (**change the two together**), bounded the
    same way — a clause break
    or ``MAX_MARKER_DISTANCE``, whichever comes first. What it reads backwards
    is the reason half of a Korean causal sentence: 때문에 and 탓에 sit *before*
    the predicate they explain, so the connective that makes "막혀" a
    ``blocked_by`` is behind the cue rather than in front of it. #254.
    """
    start = max(0, index - MAX_MARKER_DISTANCE)
    window = text[start:index]
    cut = 0
    for boundary in _CLAUSE_BREAKS:
        found = _rfind_boundary(window, boundary)
        if found != -1:
            cut = max(cut, found + len(boundary))
    return start + cut, window[cut:]


def _rfind_boundary(window: str, boundary: str) -> int:
    """Where ``boundary`` last ends a clause in ``window``, or ``-1``.

    ``_find_boundary`` read backwards, including its exception: a 고 preceded by
    다 is the quotative ending inside one clause and not a boundary, so the
    search steps over it and carries on leftwards.

    **Change the two together.** They are the same rule twice with the
    direction reversed, and a boundary fixed on one side only would leave a
    clause that ends where the other half does not agree it does. Raised in
    review of #254.
    """
    end = len(window)
    while True:
        found = window.rfind(boundary, 0, end)
        if found == -1:
            return -1
        if boundary.startswith("고") and found > 0 and window[found - 1] == "다":
            end = found
            continue
        return found


def _causal_in(clause: str) -> int | None:
    """Where the reason-heading connective nearest the end of ``clause`` begins.

    ``_CAUSAL_BEFORE``, not ``_CAUSAL``: a verb ending behind the cue closes its
    own clause, and this window would read through it.

    Nearest the end, because that is the one the cue is reading. "캐시 때문에
    인덱스 문제 때문에 막혀 있습니다" offers two reasons and the second is the
    one attached to 막혀; taking the first would reach past a reason the speaker
    already closed.
    """
    at = max(clause.rfind(connective) for connective in _CAUSAL_BEFORE)
    return at if at != -1 else None


def _reason_before(clause: str, at: int) -> str:
    """What the connective at ``at`` offers as its reason, and nothing earlier.

    Bounded on the left by the previous reason-heading connective, because a
    backward window can hold more than one: "캐시 처리 때문에 인증 탓에 막혀
    있습니다" states two, and 처리 belongs to the first. Handing the whole window
    to ``_resolved`` read that 처리 as this reason's resolution and dropped 인증,
    the blocker actually standing — which is wider than the 처리 ambiguity this
    module accepts, and in the same losing direction for a sentence that never
    had it. Raised in review of #254 by @lsh2217.
    """
    previous = 0
    for connective in _CAUSAL_BEFORE:
        found = clause.rfind(connective, 0, at)
        if found != -1:
            previous = max(previous, found + len(connective))
    return clause[previous:at]


def _find_boundary(window: str, boundary: str) -> int:
    """Where ``boundary`` first ends a clause in ``window``, or ``-1``.

    Not ``str.find``, because of ``-다고``. The quotative ending puts a 고 in
    the middle of one clause — "인덱스가 필요하다고 보시나요" is a single
    question — and reading it as a boundary cut the window before the 나요, so
    the question came back as an asserted dependency. A 고 preceded by 다 is
    skipped and the search goes on; every other 고 still ends a clause, which is
    what "필요하고 캐시는" needs. Raised in review of #249 by @kjfcvx12.
    """
    start = 0
    while True:
        found = window.find(boundary, start)
        if found == -1:
            return -1
        if boundary.startswith("고") and found > 0 and window[found - 1] == "다":
            start = found + 1
            continue
        return found


def _asserted(text: str, cue_end: int) -> bool:
    """Whether what follows the cue lets it stand as a statement.

    A marker is a string and a clause is what decides whether the speaker meant
    it: 필요 with 없 after it in the same clause is the opposite of a need, and
    필요 with 나요 after it is a question about one. Both produced a
    ``depends_on`` edge before, and ``blocked_by`` has the same hole — "캐시
    이슈는 없어서 검색 기능은 바로 진행합니다" is a blocker that is not there.

    The window stops at the clause boundary on purpose. Reading to the end of
    the utterance would let "인덱스가 필요하고 캐시는 문제 없습니다" cancel a
    need the speaker did state. Raised in review of #249.
    """
    clause = _clause_after(text, cue_end)
    return not any(marker in clause for marker in _NEGATIONS + _QUESTIONS)


def _resolved(clause: str) -> bool:
    """Whether the blocker in this clause was said to be gone.

    The resolution has to stand on its own: "해결이 안 되어서" contains 해결 and
    resolves nothing, so anything in ``_UNDONE`` after the word takes it back.
    Looking only after it is deliberate — what comes before belongs to the
    blocker ("이슈 해결" is the thing, "해결이 안 되어서" is the state).

    Run on whichever clause carries the causal connective, which since #254 may
    be the one behind the cue. That doubles what the 처리 ambiguity costs:
    "캐시 처리 때문에 막혀 있습니다" names the work and reads as a resolution, so
    a blocker the meeting did state is dropped. Kept, because precision is C's
    metric and ``blocked_by`` is a finding in its own right — a blocker nobody
    asserted costs more than one that goes missing. Pinned in
    ``test_relations.py``.
    """
    for word in _RESOLVED:
        at = clause.find(word)
        if at != -1 and not any(undone in clause[at:] for undone in _UNDONE):
            return True
    return False


def _directed_markers(text: str) -> list[tuple[int, str]]:
    """``(offset, relation)`` for every need or blocker marker in ``text``."""
    markers: list[tuple[int, str]] = []
    for cue in _NEEDS:
        markers.extend(
            (match.start(), "depends_on")
            for match in _finditer(cue, text)
            if _asserted(text, match.end())
        )
    for cue in _BLOCKERS:
        for match in _finditer(cue, text):
            # A blocker is a relation only when it is offered as the reason, and
            # only when the reason is in the same clause. Without the connective
            # the speaker described a state; with one two clauses away, "이슈는
            # 어제 처리했고요 … 시간이 없어서" paired a blocker with somebody
            # else's reason and put a date topic on the blocked end. Raised in
            # review of #249.
            if not _asserted(text, match.end()):
                continue
            after = _clause_after(text, match.end())
            if _resolved(after):
                continue
            if any(connective in after for connective in _CAUSAL):
                markers.append((match.start(), "blocked_by"))
                continue
            # The connective may also sit *behind* the cue, and for three of
            # the six it always does: 어서/아서/라서 attach to the predicate
            # ("안 잡혀 있어서") while 때문에/탓에/으로 인해 precede it
            # ("캐시 때문에 막혀"). Reading only forwards dropped every relation
            # a meeting stated the second way. Only those three are read
            # backwards (``_CAUSAL_BEFORE``) -- a verb ending behind the cue
            # ends the previous clause, and this window would read past it.
            # #254.
            start, before = _clause_before(text, match.start())
            at = _causal_in(before)
            if at is None:
                continue
            if any(marker in before[at:] for marker in _REASON_DENIED):
                continue
            if _resolved(_reason_before(before, at)):
                continue
            # The marker is the connective, not the cue. The thing in the way is
            # what the reason clause names — "캐시 때문에 정렬 로직이 막혀
            # 있습니다" blocks on 캐시, and the mention before the cue is 정렬
            # 로직, the thing being blocked.
            markers.append((start + at, "blocked_by"))
    return sorted(markers)


def _ends_around(text: str, mentions: Sequence[Mention], marker: int) -> tuple[str, str] | None:
    """``(source, target)`` for a marker at offset ``marker``, or ``None``.

    The target — the thing needed, or the thing in the way — is the mention
    ending closest before the marker. Both Korean word orders put it there.

    The source is what the clause is about, and it is looked for in this order:

    1. **The nearest mention after the marker, if it is still the same clause.**
       "인덱스가 있어야 정렬 로직을 붙입니다" puts it there. Taking it across a
       clause boundary is what made "정렬 로직은 인덱스가 필요하고 캐시는 다음
       주에 봅시다" say that 캐시 needs 인덱스.
    2. **The nearest preceding mention wearing 은/는**, which is how Korean
       marks the thing a sentence is about. Without this the default was simply
       the mention before the target, and in "정렬 로직은 인덱스에 의존하지
       않고 캐시 없이는 안 됩니다" that is 인덱스 — a topic sitting inside the
       clause the sentence has just denied.
    3. **The mention before the target**, when nothing is marked.

    All three raised in review of #249.
    """
    before = [mention for mention in mentions if mention.end <= marker]
    if not before or marker - before[-1].end > MAX_MARKER_DISTANCE:
        return None
    target = before[-1]

    clause = _clause_after(text, marker)
    after = [mention for mention in mentions if mention.start >= marker]
    if after and after[0].start < marker + len(clause):
        # Strictly inside: a mention that begins exactly where the clause ends
        # is the next clause's subject, which is the case this rule exists to
        # refuse.
        return after[0].text, target.text

    marked = [
        mention for mention in before[:-1] if text[mention.end : mention.end + 1] in _TOPIC_MARKERS
    ]
    if marked:
        return marked[-1].text, target.text
    if len(before) < 2:
        return None
    return before[-2].text, target.text


def _contrast_pairs(text: str, mentions: Sequence[Mention]) -> list[tuple[str, str, str]]:
    """Adjacent mentions with a contrast marker between them, both ways.

    Symmetric relations are stored as two rows (``base.SYMMETRIC_RELATIONS``),
    and producing both here keeps ``graph`` from having to know which relations
    are which.
    """
    pairs: list[tuple[str, str, str]] = []
    for left, right in zip(mentions, mentions[1:], strict=False):
        gap = text[left.end : right.start]
        if len(gap) > MAX_MARKER_DISTANCE or not _ALTERNATIVES.search(gap):
            continue
        pairs.append((left.text, right.text, "alternative_to"))
        pairs.append((right.text, left.text, "alternative_to"))
    return pairs


def _finditer(cue: str, text: str) -> list[re.Match[str]]:
    """Occurrences of ``cue``, tolerant of the spacing between its characters —
    a speaker's "안 잡" reaches the transcript as "안잡" and "안  잡" often
    enough to matter."""
    pattern = re.compile(
        r"\s*".join(re.escape(character) for character in cue if character.strip())
    )
    return list(pattern.finditer(text))


class RuleRelations:
    """The rules above, over a meeting's entities. Deterministic, local, no model.

    ``model_version`` is a hand-set string rather than a package version: what
    decides this implementation's output is the marker lists in this file, and
    they change without anything else changing. Bump it when a rule changes, so
    a graph built before the change can be told from one built after — the same
    reason ``SpacyNer`` records the pipeline version.
    """

    model_version = "rules-2"

    def extract(self, utterances: list[tuple[str, str]], entities: list[Entity]) -> list[Relation]:
        """Relations in each utterance, against every name the meeting used.

        **The names come from the whole meeting, not from this utterance's own
        entities**, and that is what makes the rules fire at all. Entity
        extraction claims a bare noun run and stops at a particle, so the
        utterance that states the relation is the one where the topic wears one:
        the meeting says 실시간 개인화 in one breath and 실시간은 in the next,
        and it is the second one — the one with 은 on it — that says what is
        blocking it. Keyed on this utterance's own entities the rules saw one
        end of every relation and never the other.

        It does not put a topic in the graph. Topics and their evidence come
        from ``build_topics`` over the entities, and ``graph.relation_edges``
        drops any relation whose ends are not both topics. What this widens is
        only which utterances can *state* a relation between them.
        """
        names = {entity.text for entity in entities}
        found: list[Relation] = []
        for utterance_id, text in utterances:
            spans = mention_spans(text, names)
            found.extend(
                Relation(source=source, target=target, relation=relation, utterance_id=utterance_id)
                for source, target, relation in relations_in(text, spans)
            )
        return found
