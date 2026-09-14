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
from collections.abc import Sequence
from dataclasses import dataclass

from autune_contracts.enums import UtteranceKind
from autune_core.ids import DECISION

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


@dataclass(frozen=True)
class DecisionGroup:
    """One decision, before it is given an id or written down."""

    statement: str
    source_utterance_ids: tuple[str, ...]
    confidence: float


def decision_id(meeting_id: str, source_utterance_ids: Sequence[str]) -> str:
    """The ``dec_`` id of a decision, derived from where it was settled (#171).

    ``dec_`` followed by the first 32 hex digits of a SHA-256 over the meeting
    id and the source utterance ids in meeting order -- the same shape
    ``new_id`` gives, so nothing downstream can tell the two apart.

    **The same sources give the same id, however many times the meeting is
    rebuilt.** A random id changed on every rerun: a redelivery, a new
    classifier, a reprocess after a correction. Module D keys a lineage on
    ``(meeting, dec_)``, and an id that moved on every rebuild never matched,
    so D had to fall back on comparing wording.

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
    utterances: Sequence[ClassifiedUtterance], *, max_gap: int = DEFAULT_MAX_GAP
) -> list[DecisionGroup]:
    """Group decision-labelled utterances into decisions, in meeting order.

    ``utterances`` is *every* utterance of the meeting, ordered by ``start_sec``
    — not only the decision-labelled ones. The utterances in between are what
    the gap is measured in, so filtering them out first would merge every
    decision in the meeting into one.

    Raises ``ValueError`` on a negative ``max_gap``: a caller reaching for one is
    asking for behaviour this has none of, and silently clamping it to zero would
    hide that.
    """
    if max_gap < 0:
        raise ValueError(f"max_gap must not be negative, got {max_gap}")

    groups: list[DecisionGroup] = []
    current: list[ClassifiedUtterance] = []
    since_last = 0

    for utterance in utterances:
        if utterance.kind is UtteranceKind.DECISION:
            current.append(utterance)
            since_last = 0
            continue

        if not current:
            continue

        since_last += 1
        if since_last > max_gap:
            groups.append(_build(current))
            current = []

    if current:
        groups.append(_build(current))
    return groups


def _build(members: Sequence[ClassifiedUtterance]) -> DecisionGroup:
    """Turn one run of decision utterances into a decision.

    **Statement** is the last member. The contract asks for "the decision as
    settled", and a run settles at its end — the earlier members are the proposal
    being converged on, and quoting one of those would publish a version of the
    decision the meeting moved past. Taking the most confident member instead is
    the other defensible reading; the evaluation set decides, not this comment.

    This is a quotation, and the contract asks for one sentence of prose. Step 2
    (reference resolution) is what turns "그럼 그걸로 가시죠" into a statement that
    reads on its own, and it is not built yet (#11). Quoting is wrong in a way a
    reader can see; a generated sentence would be wrong in a way they could not.

    **Confidence** is the highest member's, not the mean. Averaging would score a
    decision lower the more turns it took to reach, which is backwards — spanning
    several utterances is the case this entity exists for, and the extra turns
    are corroboration, not doubt.
    """
    return DecisionGroup(
        statement=members[-1].text,
        source_utterance_ids=tuple(member.id for member in members),
        confidence=max(member.confidence for member in members),
    )
