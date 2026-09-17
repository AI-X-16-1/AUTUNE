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

_NEEDS = ("필요", "있어야", "되어야", "돼야", "선행", "전제", "없이는", "없으면")
"""What a speaker says when one thing waits on another.

``없이는``/``없으면`` are here rather than in ``_BLOCKERS`` because they state a
condition, not a state: "인덱스 없으면 정렬 로직은 못 붙여요" says what the
meeting needs, and the graph can carry that without claiming anybody is blocked
today.
"""

_ALTERNATIVES = ("대신", "말고", "보다는", "아니라", "반면", "vs", "versus")
"""Markers that weigh one topic against another.

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

_GENITIVE = "의"
"""``A의 B`` — B belongs to A.

The one rule that needs adjacency rather than a distance: the particle has to
sit in the gap between the two mentions and nothing else may. "검색의 정렬
로직" is a genitive; "검색 기능은 정렬 로직의 문제입니다" also contains 의 and
joins a different pair.
"""


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
    """
    claimed: list[Mention] = []
    for mention in sorted({m for m in mentions if m.strip()}, key=len, reverse=True):
        pattern = re.compile(r"\s+".join(re.escape(part) for part in mention.split()))
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < other.end and other.start < end for other in claimed):
                continue
            claimed.append(Mention(text=mention, start=start, end=end))
    return sorted(claimed, key=lambda mention: mention.start)


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
      so a causal connective (``_CAUSAL``) has to follow it **in the same
      clause**, and the same assertion guard applies: "캐시 이슈는 없어서" is a
      blocker that is not there. Ends are read the same way.
    - **``part_of``** — ``A의 B``, adjacent, nothing but the particle between.
    - **``alternative_to``** — a contrast marker in the gap between two
      mentions, and nowhere else.

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
    found.extend(_genitive_pairs(text, mentions))
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
        found = window.find(boundary)
        if found != -1:
            cut = min(cut, found + len(boundary))
    return window[:cut]


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
            clause = _clause_after(text, match.end())
            if any(connective in clause for connective in _CAUSAL):
                markers.append((match.start(), "blocked_by"))
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


def _genitive_pairs(text: str, mentions: Sequence[Mention]) -> list[tuple[str, str, str]]:
    """``A의 B`` for every adjacent pair, as ``B part_of A``."""
    return [
        (right.text, left.text, "part_of")
        for left, right in zip(mentions, mentions[1:], strict=False)
        if text[left.end : right.start].strip() == _GENITIVE
    ]


def _contrast_pairs(text: str, mentions: Sequence[Mention]) -> list[tuple[str, str, str]]:
    """Adjacent mentions with a contrast marker between them, both ways.

    Symmetric relations are stored as two rows (``base.SYMMETRIC_RELATIONS``),
    and producing both here keeps ``graph`` from having to know which relations
    are which.
    """
    pairs: list[tuple[str, str, str]] = []
    for left, right in zip(mentions, mentions[1:], strict=False):
        gap = text[left.end : right.start]
        if len(gap) > MAX_MARKER_DISTANCE or not any(marker in gap for marker in _ALTERNATIVES):
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

    model_version = "rules-1"

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
