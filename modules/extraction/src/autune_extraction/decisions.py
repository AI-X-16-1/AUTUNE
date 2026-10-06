"""Step 5: group the utterances a classifier marked ``decision`` into entities.

A ``Classification`` marks one utterance. A decision is an entity that usually
spans several — a proposal, some back-and-forth, and the sentence that settles
it — and module D keys a decision lineage on that entity, not on the labels.
Without it D has nothing to attach a ``thr_`` thread to. See
``docs/architecture/contracts.md``, "The B -> D boundary".

Grouping is arithmetic over labels, not model inference, and it writes nothing,
so it lives here rather than in ``pipeline`` (which loads models) or ``service``
(which holds the session). That keeps it testable without either.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from autune_contracts.enums import UtteranceKind
from autune_core.ids import DECISION

from .noun_form import tidy
from .slots import parse_due

DEFAULT_MAX_GAP = 2
"""How many non-decision utterances may sit between two decision utterances
before they are treated as two separate decisions.

Counted in utterances rather than seconds: a pause is not a topic change, and a
meeting that goes quiet mid-decision should not have it split in two.

The value is a guess and is meant to be measured. It decides the one thing this
step can get wrong in both directions — too low fragments one decision into
several near-duplicate entities that D then has to reconcile, too high welds two
decisions into one statement that fits neither. ADR 0006's evaluation set is
what would settle it; until then it is a named constant rather than a literal so
the argument has somewhere to live.
"""


@dataclass(frozen=True)
class ClassifiedUtterance:
    """One utterance with the label the classifier gave it.

    ``kind`` is ``None`` for an utterance the model calls none of the kinds
    (#149). Those still belong in the sequence ``group_decisions`` reads: they
    are most of the utterances between two decisions, and the gap is counted in
    them.

    ``text`` is already PII-masked — module A masks before the first write and
    there is no unmasked column to read (invariant 11, privacy.md section 2).
    """

    id: str
    kind: UtteranceKind | None
    confidence: float
    text: str
    speaker: str = ""
    """The label the transcript had for whoever said this, for the owner of a
    decision. Defaults to empty so a caller that does not know it still builds
    one; the statement then carries no owner rather than a wrong one."""
    nli_verified: bool = False
    """Whether step 4 (``service.verify_utterances``) actually ran NLI on this
    utterance -- true for an ``ambiguous`` row after that step (whether or not
    it was promoted to ``commitment``), false for every other kind and for
    anything built before it exists. Carried on the utterance itself, not a
    side collection, because every later step that reads ``kind``
    (``build_action_items``, ``record_ambiguous_agreements``,
    ``store_classifications``) reads this the same way."""
    pieces: tuple[tuple[str, UtteranceKind | None], ...] = ()
    """For a turn the classifier asked about in pieces: each piece's text and
    kind, in order (``Prediction.pieces``). ``kind`` above is the one the
    utterance is stored with; ``in_pieces`` is how the rest of the run reads
    the turn."""
    part_of: str = ""
    """The id of the utterance this is a piece of, for an entry ``in_pieces``
    made; empty for an utterance itself."""

    @property
    def source_id(self) -> str:
        """The utterance a row built from this cites: itself, or the one it is
        a piece of. The only id that is ever stored or leaves the module."""
        return self.part_of or self.id


def in_pieces(classified: Sequence[ClassifiedUtterance]) -> list[ClassifiedUtterance]:
    """The meeting's sequence with every turn that was classified in pieces
    replaced by its pieces, each an entry of its own.

    One person talking for minutes is one utterance and can hold a decision,
    two promises and a good deal else. Read as one line it gave one item whose
    description was the whole turn -- too long for the resolver to summarise
    -- and lost whatever its one kind was not (the user, 2026-10-06: an item
    for each). So the resolver, the decision grouping and the item builder
    read the pieces: each piece is a line with its own kind and text, a target
    short enough to summarise, and context for the lines around it.

    A piece's ``id`` is the utterance's with ``#n`` after it. It is the key a
    summary is found by and what a decision's id is derived from -- so two
    decisions settled in one turn are two rows -- and it is never stored and
    never leaves: every row cites ``source_id``.

    A turn whose kind no piece has was changed after classification (step 4
    promotes an ambiguous agreement to a commitment); it stays whole, as the
    step that changed it read it.
    """
    out: list[ClassifiedUtterance] = []
    for utterance in classified:
        if not utterance.pieces or utterance.kind not in {kind for _, kind in utterance.pieces}:
            out.append(utterance)
            continue
        for number, (text, kind) in enumerate(utterance.pieces, start=1):
            out.append(
                replace(
                    utterance,
                    id=f"{utterance.id}#{number}",
                    kind=kind,
                    text=text,
                    pieces=(),
                    part_of=utterance.id,
                )
            )
    return out


@dataclass(frozen=True)
class DecisionGroup:
    """One decision, before it is given an id or written down."""

    statement: str
    """What a person sees and what leaves: the noun-ended line (``noun_form.tidy``),
    or a model's summary of it when the resolver writes one."""

    source_utterance_ids: tuple[str, ...]
    confidence: float

    original_statement: str = ""
    """The same sentence *before* ``noun_form.tidy`` -- the substance turn as said,
    with the owner and deadline -- which is what module D has always been sent. D
    embeds and compares statements against a similarity threshold tuned on this
    shape (``context.config``), so a rewrite for the screen must not reach it.
    Empty only for a group built by a caller that does not know it; read it as
    ``original_statement or statement``."""

    core_text: str = ""
    """The substance turn's own text, which a summary is written from."""

    suffix: str = ""
    """``담당 박지영, 기한 2026-10-02``, or empty: what is added after the line."""

    substance_id: str = ""
    from_piece: bool = False
    """Whether the substance is a piece of a long turn (``in_pieces``): up to
    half a request of speech, which is not a line of minutes until a model has
    written what was decided in it (``needs_write_up``)."""
    first_position: int = 0
    last_position: int = 0
    """Where the decision's first and last utterance sit in the meeting's sequence,
    so a summary can be given the lines around it."""


def decision_id(meeting_id: str, source_utterance_ids: Sequence[str]) -> str:
    """The ``dec_`` id of a decision, derived from where it was settled (#171).

    ``dec_`` followed by the first 32 hex digits of a SHA-256 over the meeting
    id and the source utterance ids in meeting order -- the same shape
    ``new_id`` gives, so nothing downstream can tell the two apart.

    **The same sources give the same id, however many times B rebuilds the
    meeting.** A random id changed on every rerun of B -- a redelivery, a new
    classifier -- and module D stores ``(meeting, dec_)`` with each version, so
    an id that moved on every rebuild pointed at nothing.

    **Not closed here: a reprocess in module A.** A replaces the meeting's
    utterances and mints new ``utt_`` ids (#194), so every source id changes and
    every ``dec_`` id derived from them changes with it. Whether utterance ids
    survive a reprocess is A's decision; this function only promises that B
    adds no instability of its own.

    **Different sources give a different id.** A decision that now spans other
    utterances is, as far as B can tell, a different decision; whether it is
    the same one in other words is the question #25 gave to D. The order is
    meeting order, not sorted: two runs over the same labels produce the same
    order, and an order that changed would mean the grouping did.

    The meeting id is in the hash because an utterance id is unique on its own
    but a decision is only ever this meeting's. The ids are hashed as a JSON
    array rather than joined with a separator, so no id can be read as two.
    """
    encoded = json.dumps([meeting_id, *source_utterance_ids], ensure_ascii=False)
    return f"{DECISION}_{hashlib.sha256(encoded.encode()).hexdigest()[:32]}"


def group_decisions(
    utterances: Sequence[ClassifiedUtterance],
    *,
    max_gap: int = DEFAULT_MAX_GAP,
    day: date | None = None,
) -> list[DecisionGroup]:
    """Group decision-labelled utterances into decisions, in meeting order.

    ``utterances`` is *every* utterance of the meeting, ordered by ``start_sec``
    — not only the decision-labelled ones. The utterances in between are what
    the gap is measured in, so filtering them out first would merge every
    decision in the meeting into one. They are also where the owner and the
    deadline are said, so each decision keeps the slice it spans and ``_build``
    reads the whole of it.

    ``day`` is the meeting's date in Korea (``slots.meeting_day``), and only
    relative deadlines need it — "이번 주 금요일" is a different Friday every
    week. Without it a decision keeps the phrase instead of a date.

    Raises ``ValueError`` on a negative ``max_gap``: a caller reaching for one is
    asking for behaviour this has none of, and silently clamping it to zero would
    hide that.
    """
    if max_gap < 0:
        raise ValueError(f"max_gap must not be negative, got {max_gap}")

    groups: list[DecisionGroup] = []
    current: list[int] = []
    since_last = 0

    def close() -> None:
        members = [utterances[i] for i in current]
        region = utterances[current[0] : current[-1] + 1]
        groups.append(_build(members, region, day=day, span=(current[0], current[-1])))

    for position, utterance in enumerate(utterances):
        if utterance.kind is UtteranceKind.DECISION:
            current.append(position)
            since_last = 0
            continue

        if not current:
            continue

        since_last += 1
        if since_last > max_gap:
            close()
            current = []

    if current:
        close()
    return groups


MIN_SUBSTANCE = 12
"""Below this many characters a settling utterance is treated as assent, not as
the decision. "그렇게 하죠", "네 그 방향으로" — true of the decision and useless as
a record of it. Measured on the team meetings written for the register work: the
row carrying ``settles`` is a median 38 characters, but the ones that settle by
agreeing rather than by restating are nearly all under twelve."""

_POINTS_AT = re.compile(r"(그거|그건|그걸|그게|저거|그 부분|그 건|그대로|그 방향|그렇게|이대로)")
"""Words that stand in for something said earlier. A settling row made only of
these says nothing on its own however long it is."""

_ASKS = re.compile(r"([가-힣]{2,4})\s?(?:씨|님)(?:가|께서|이)?\s.*(?:주세요|주시|부탁|맡아)")
"""Naming somebody while handing them the work: "지영 씨가 저번처럼 해주세요".

Both halves are required. The name is at least two syllables, so "날씨가" and
"손님이" are not people; and the sentence has to ask for something, so "고객님이
원하시니" names nobody as the owner."""


SELF_CONTAINED = 20
"""A substance turn this long that points at nothing ("그거", "그 방향") already says
what was decided; writing it up again would only change its ending. See
``needs_write_up``."""


def needs_write_up(group: DecisionGroup) -> bool:
    """Whether a model is worth asking about this decision.

    Measured on the dummy meetings (2026-09-30): asked about every decision, the
    model rewrote all of them and cited a line for about a quarter -- the rest it
    only put into "~하기로 했습니다", which ``noun_form.tidy`` does without a model,
    a request or a chance of drifting from what was said. It earns its call when
    the turn that settles the decision does not say what was decided: it is short,
    or it points at something said before.
    """
    core = group.core_text.strip()
    return group.from_piece or len(core) < SELF_CONTAINED or bool(_POINTS_AT.search(core))


def _substance(members: Sequence[ClassifiedUtterance]) -> ClassifiedUtterance:
    """The member that says *what* was decided, not the one that says yes.

    A decision usually ends in assent — the substance is in the turn being
    assented to. So: the settling member when it stands on its own, otherwise the
    longest earlier member, which is the proposal it agreed to.
    """
    settling = members[-1]
    stripped = _POINTS_AT.sub("", settling.text)
    if len(stripped.strip()) >= MIN_SUBSTANCE:
        return settling
    earlier = members[:-1]
    return max(earlier, key=lambda m: len(m.text)) if earlier else settling


def _owner(region: Sequence[ClassifiedUtterance], substance: ClassifiedUtterance) -> str | None:
    """Who is on the hook, when the meeting made that recoverable.

    Two ways it is said, in the order they are trusted:

    1. Somebody took it on — the last ``commitment`` in the region is the person
       who said they would do it.
    2. Somebody was handed it by name — "지영 씨가 저번처럼 해주세요" in the
       decision itself.

    ``None`` when neither happened, which is most of the time and is the honest
    answer: a decision with nobody attached is a real outcome of a real meeting.
    """
    for utterance in reversed(region):
        if utterance.kind is UtteranceKind.COMMITMENT and utterance.speaker:
            return utterance.speaker
    named = _ASKS.search(substance.text)
    return named.group(1) if named else None


def _build(
    members: Sequence[ClassifiedUtterance],
    region: Sequence[ClassifiedUtterance] = (),
    *,
    day: date | None = None,
    span: tuple[int, int] = (0, 0),
) -> DecisionGroup:
    """Turn one run of decision utterances into a decision.

    **Statement is what a person would write in the minutes**, assembled from the
    meeting rather than generated: the turn that carries the substance, plus the
    owner and the deadline when the talk made them recoverable. Before 2026-09-21
    it was the last member quoted verbatim, which in these meetings reads "그럼
    그 방향으로 가시죠" — true, and unusable as a record. The owner is usually
    three turns away and the deadline is relative to another date, so neither is
    in the quoted row.

    It is still assembled from what was said, never written by a model. A
    generated sentence would be wrong in a way the reader could not see; this is
    wrong in a way they can, and ``source_utterance_ids`` is what they check it
    against. The one rewrite is ``noun_form.tidy``, which puts the sentence's
    ending in the noun form a record uses -- a fixed list of endings, and a
    sentence none of them fits is left as it was said. The original utterances
    are shown beside it on S15, and a person confirms or rewords the line before
    anything leaves.

    ``region`` is every utterance from the first member to the last, the
    non-decision ones included — that is where the commitment naming the owner
    and the phrase naming the deadline actually sit. It defaults to empty, which
    gives the old behaviour of reading the members alone.

    **Confidence** is the highest member's, not the mean. Averaging would score a
    decision lower the more turns it took to reach, which is backwards — spanning
    several utterances is the case this entity exists for, and the extra turns
    are corroboration, not doubt.
    """
    scope = list(region) or list(members)
    substance = _substance(members)
    core = substance.text.strip()
    extras = []

    owner = _owner(scope, substance)
    if owner:
        extras.append(f"담당 {owner}")

    for utterance in scope:
        if (due := parse_due(utterance.text, day)) is not None:
            extras.append(f"기한 {due.date.isoformat()}" if due.date else f"기한 {due.text}")
            break

    suffix = ", ".join(extras)

    def line(head: str) -> str:
        return f"{head} ({suffix})" if suffix else head

    return DecisionGroup(
        statement=line(tidy(core)),
        source_utterance_ids=tuple(member.id for member in members),
        confidence=max(member.confidence for member in members),
        original_statement=line(core),
        core_text=core,
        suffix=suffix,
        substance_id=substance.id,
        from_piece=bool(substance.part_of),
        first_position=span[0],
        last_position=span[1],
    )
