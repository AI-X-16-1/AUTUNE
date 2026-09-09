"""The AMI annotation layers, mapped to the five kinds this module classifies.

``docs/modules/extraction.md`` states the mapping as a table. This is that table
as code, because three different things need it — the corpus loader, the LLM
labelling prompt, and the hand-correction pass — and three paraphrases of a table
drift apart.

AMI splits the evidence across layers that have to be read together. The act
inventory carries no polarity at all: ``Assess`` is the largest task act and is
unsigned, which is why ``concern`` is keyed on the adjacency pairs instead of on
an act. One utterance can therefore carry evidence from several layers at once,
and ``PRECEDENCE`` is what settles that.

Attribution: AMI Meeting Corpus, CC BY 4.0.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from autune_contracts.enums import UtteranceKind

DIALOGUE_ACTS: Final[Mapping[str, UtteranceKind]] = MappingProxyType(
    {
        "Offer": UtteranceKind.COMMITMENT,
        "Elicit-Inform": UtteranceKind.OPEN_QUESTION,
        "Elicit-Offer-Or-Suggestion": UtteranceKind.OPEN_QUESTION,
        "Elicit-Assessment": UtteranceKind.OPEN_QUESTION,
        "Elicit-Comment-Understanding": UtteranceKind.OPEN_QUESTION,
    }
)
"""AMI dialogue acts that map to a kind. The other ten map to nothing.

All four ``Elicit-*`` acts are one class here. AMI separates them by what is being
asked for; this module only needs that something was asked and left open.
"""

EXCLUDED_ACTS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "Suggest": (
            "A proposal, not a commitment. It outnumbers Offer six to one, so "
            "folding it in would swamp the commitment class with things nobody "
            "agreed to do."
        ),
        "Assess": (
            "The largest task act, and unsigned — it covers both 'that works' and "
            "'that will never work'. Concern is keyed on adjacency-pair polarity "
            "for this reason."
        ),
        "Be-Positive": "A social move, not a position on the task.",
        "Be-Negative": "Likewise. Not the same thing as raising a concern.",
        "Inform": "Carries no illocutionary force any of the five kinds needs.",
        "Comment-About-Understanding": "About the conversation, not about the work.",
        "Backchannel": "'mm-hm'. Assent this weak is what the ambiguous class is for.",
        "Stall": "Filler.",
        "Fragment": "Incomplete.",
        "Other": "AMI's catch-all.",
    }
)
"""Acts deliberately left unmapped, and why.

Written down rather than omitted: the next person to look at recall on the
commitment class will reach for ``Suggest``, and the reason not to is a measured
one that belongs next to the decision.
"""

ADJACENCY_PAIRS: Final[Mapping[str, UtteranceKind]] = MappingProxyType(
    {
        "apt_2": UtteranceKind.CONCERN,  # NEG  Objection/Negative
        "apt_3": UtteranceKind.AMBIGUOUS,  # UNC  Uncertain
        "apt_4": UtteranceKind.AMBIGUOUS,  # PART Partial agreement
    }
)
"""Adjacency-pair polarity types that map to a kind.

``apt_1`` (POS) and ``apt_5`` (ELA) map to nothing. Plain agreement is not a
commitment — treating it as one is the failure the confirmation DM exists to
catch — and an elaboration takes its kind from what it elaborates.
"""

DECISION_SPAN: Final = "decision"
"""AMI's extractive decision layer: word spans a human marked as where a decision
was made. Not an act and not a polarity, so it is passed separately."""

PRECEDENCE: Final[tuple[UtteranceKind, ...]] = (
    UtteranceKind.DECISION,
    UtteranceKind.CONCERN,
    UtteranceKind.AMBIGUOUS,
    UtteranceKind.COMMITMENT,
    UtteranceKind.OPEN_QUESTION,
)
"""Which kind wins when the layers disagree, highest first.

**This ordering is not in the corpus documentation and not in ours.** It is a
judgement, and the reasoning is below so it can be argued with rather than
guessed at. ``label_for`` reports what it overruled so the first real corpus pass
measures how often any of this matters.

``decision`` first — an extractive decision span is the most specific human
judgement in the corpus, a person pointing at where the meeting settled
something. It is also the field module D keys a lineage on, so a decision
demoted to something else costs more than the reverse.

``concern`` over ``ambiguous`` — an utterance that is both uncertain and an
objection is an objection. The ambiguous class exists to trigger a "did you mean
to commit?" DM, and asking that about a stated objection is worse than useless:
it was not assent at all.

``concern`` and ``ambiguous`` over ``commitment`` — polarity is a fact about how
an utterance answers the one before it, and the act inventory carries no sign, so
polarity is the *more* informed source here, not the less. An ``Offer`` inside a
NEG or UNC pair is "한번 볼게요": the shape of a commitment without the substance,
and calling it a commitment is exactly the error ADR 0006 asks us to catch.

``open_question`` last — it is the only kind that describes what the utterance
asks for rather than what it settles, so anything else known about the utterance
is more useful downstream.
"""


@dataclass(frozen=True)
class Evidence:
    """What the corpus layers say about one utterance.

    Every field is optional because most utterances carry none of it: AMI's
    largest acts map to nothing, and only a minority of utterances sit inside a
    decision span or an adjacency pair.
    """

    in_decision_span: bool = False
    dialogue_act: str | None = None
    adjacency_pair_type: str | None = None


@dataclass(frozen=True)
class Label:
    """One labelled utterance, and what the label cost.

    ``overruled`` is empty for almost every utterance. When it is not, the corpus
    layers disagreed and ``PRECEDENCE`` picked — recording it is what lets a
    corpus pass count the disagreements instead of the ordering being an
    unexamined constant.
    """

    kind: UtteranceKind
    source: str
    overruled: tuple[UtteranceKind, ...] = ()

    @property
    def is_contested(self) -> bool:
        return bool(self.overruled)


def label_for(evidence: Evidence) -> Label | None:
    """The kind this utterance should train as, or ``None`` if the corpus is silent.

    ``None`` is the common case and not a failure. Most utterances in a meeting
    are none of these five things, and a loader that forced a label on them would
    teach the model that everything is a commitment.
    """
    candidates: dict[UtteranceKind, str] = {}

    if evidence.in_decision_span:
        candidates[UtteranceKind.DECISION] = DECISION_SPAN

    act_kind = DIALOGUE_ACTS.get(evidence.dialogue_act or "")
    if act_kind is not None:
        candidates.setdefault(act_kind, f"act:{evidence.dialogue_act}")

    pair_kind = ADJACENCY_PAIRS.get(evidence.adjacency_pair_type or "")
    if pair_kind is not None:
        candidates.setdefault(pair_kind, f"pair:{evidence.adjacency_pair_type}")

    if not candidates:
        return None

    ranked = sorted(candidates, key=PRECEDENCE.index)
    winner = ranked[0]
    return Label(kind=winner, source=candidates[winner], overruled=tuple(ranked[1:]))
