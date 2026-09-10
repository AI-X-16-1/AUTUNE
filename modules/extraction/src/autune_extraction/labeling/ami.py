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
"""AMI dialogue acts that map to a kind. The other eleven map to nothing.

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
        "Unlab": "Annotated as not annotatable. Not a class, an absence.",
    }
)
"""Acts deliberately left unmapped, and why.

Together with ``DIALOGUE_ACTS`` this accounts for all sixteen leaf acts in AMI's
``ontologies/da-types.xml``. Accounting for every one is the point: an act
nobody decided about is indistinguishable from an act somebody forgot, and the
forgetting is silent — a key that matches nothing yields an empty class, not an
error. ``scripts/ami_label_conflicts.py`` refuses to run when the two sets
disagree.

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

ASSERTIVE_ACTS: Final[frozenset[str]] = frozenset({"Inform", "Assess", "Suggest", "Offer"})
"""Acts that assert something about the task, and so can carry a decision.

The extractive decision layer marks a *region* — the stretch a human selected as
evidence that something was settled — not one utterance. Everything inside it
was being promoted to ``decision``, and a third of what that produced was
"Hmm.", "Um." and "Yeah.": 1,331 Fragments, 1,271 Backchannels and 730 Stalls,
9,835 labels from 288 annotated decisions.

Those are acts ``EXCLUDED_ACTS`` already refuses by name. The region was
overriding the exclusion, so the mapping said "a backchannel is filler" and the
loader taught the model that a backchannel is a decision.

The four here are the ones that put something on the record. ``Elicit-*`` asks
rather than asserts, and a question inside the region is a question — it does
not cost a decision entity either, because ``group_decisions`` spans a gap of
two and absorbs it into the run around it.

Membership is required *in addition* to the region, never instead of it: an
``Inform`` outside a decision span is still just an inform.
"""

DECISION_SPAN: Final = "decision"
"""AMI's extractive decision layer: word spans a human marked as where a decision
was made. Not an act and not a polarity, so it is passed separately."""

PRECEDENCE: Final[tuple[UtteranceKind, ...]] = (
    UtteranceKind.DECISION,
    UtteranceKind.CONCERN,
    UtteranceKind.OPEN_QUESTION,
    UtteranceKind.AMBIGUOUS,
    UtteranceKind.COMMITMENT,
)
"""Which kind wins when the layers disagree, highest first.

AMI does not say. This ordering was a judgement, and then
``scripts/ami_label_conflicts.py`` measured it against the corpus: 117,915
dialogue acts, 17,876 labelled, **809 of them contested (4.5%)**. Frequent
enough that the ordering decides real training data, so the counts below are
per rule rather than a total.

**Every number here is post-``ASSERTIVE_ACTS``.** An earlier version of this
docstring kept the counts from before that gate — 21,601 labelled, 1,439
contested — and said "``decision`` first, and it is most of the disagreement",
which the gate had already made false. Reasoning next to a constant is the whole
point of this file, and it went stale inside the commit that changed it.

``decision`` first, and it accounts for **365 of the 809** — 45%, not most. An
extractive decision span is the most specific human judgement in the corpus, a
person pointing at where the meeting settled something, and it is the field
module D keys a lineage on. A decision demoted to something else costs more than
the reverse.

It only outranks anything for the acts in ``ASSERTIVE_ACTS``. The span is a
region and not an utterance, so promoting everything inside it labelled "Hmm."
and "Um." as decisions — a third of the class, and every one of them an act
``EXCLUDED_ACTS`` had already refused by name. That gate is also why ``decision``
stopped being most of the disagreement: 606 of the conflicts it used to win were
``Elicit-*`` acts it should never have been promoting.

``open_question`` over ``ambiguous`` — **406 cases, the largest single rule at
50%**, and **the measurement is what put it there.** The ordering originally ran
the other way, on the reasoning that polarity is the better-informed layer.
Reading the cases it produced showed that is wrong here: they are questions, not
hedged assent.

    [Elicit-Assessment/UNC]  Do we need an L_C_D_ display?
    [Elicit-Inform/UNC]      Is that something they want actually written on it,

An ``ambiguous`` label triggers a DM asking the speaker whether they meant to
commit (#12). Asking that about a question is not a near miss — the person was
not assenting at all, and 406 of them is a steady stream of DMs that make the
product look like it is not listening.

``ambiguous`` over ``commitment`` — 5 cases. This is the "한번 볼게요" rule, and it
is right where it fires:

    [Offer/UNC]   Mm, I gotta think about it for a second like.
    [Offer/PART]  Have to think about the question,

But five. An earlier draft of this called it the load-bearing rule; it is not,
and the reason is structural rather than a quirk of this corpus. Polarity is a
property of a *response*, and an ``Offer`` is an initiating move — the two
rarely land on the same utterance in any corpus. Keep the rule, because calling
"let me see" a commitment is exactly the error ADR 0006 asks us to catch. Do not
tune anything on the strength of it.

``concern`` over the two below it — 33 cases, and mixed. "how are we going to
achieve this high-end product if" is a concern; "Uh what can a T_V_ do?" is a
question that happened to answer something negatively. Kept above
``open_question`` because a concern feeds gap detection and costs nobody a DM
when it is wrong, so the asymmetry runs the other way from the ambiguous case.

``commitment`` last, which is free: an act is either ``Offer`` or ``Elicit-*``
and never both, so it can only lose to a decision span.
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

    if evidence.in_decision_span and (evidence.dialogue_act or "") in ASSERTIVE_ACTS:
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
