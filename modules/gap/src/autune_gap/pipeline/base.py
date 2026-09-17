"""Model-facing interfaces for module C.

Every model this module runs sits behind one of these Protocols. The concrete
implementation is chosen by a config string (``AUTUNE_GAP_*_IMPL``) and is never
referenced directly outside this package — the same shape modules B and D
settled on, so the three can be read the same way.

**No implementation here sends an utterance to a third party.** Entity
extraction runs over every utterance of a meeting, so an external option would
mean handing the whole transcript to somebody else's model; ``privacy.md``
section 6 makes that a design conversation rather than a value of
``AUTUNE_GAP_NER_IMPL``. Relation extraction is the one step ``docs/modules/gap.md``
plans to give LLM assistance, and when it lands it goes through
``autune_integrations`` so ``check_outbound`` runs on the request body — a
module-local ``httpx`` client is the hole PR #74 closed and PR #90 was asked to
stop reopening.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

ENTITY_LABELS: tuple[str, ...] = ("feature", "system", "metric", "person", "date", "term")
"""What module C looks for. ``docs/modules/gap.md`` step 1 names the first five.

Deliberately not spaCy's own inventory: a model trained on news text emits
``ORG``, ``LOC`` and ``DATE``, and a meeting about a search ranking has no
"organisations" in it worth graphing. Each implementation maps its own labels
onto these, so the topic graph does not change shape when the model does.

``term`` is the sixth and says *undecided*: a compound noun the meeting named,
which is a ``feature`` or a ``system`` but which no general model can tell
apart — those two are this product's vocabulary. It exists because without it
the graph of a product meeting has no node for what the meeting was about
(``pipeline.spoken`` carries the measurement). Deciding a term's kind is what
#13's trained model is for, and until then a label that guesses would be a
guess the template comparison later reads as fact.
"""


@dataclass(frozen=True)
class Entity:
    """One thing the meeting talked about, and where it was said.

    ``text`` comes from an utterance module A already masked — there is no
    unmasked column to read (invariant 11, privacy.md section 2), and nothing
    here re-derives from one.

    ``utterance_id`` is carried so the topic a graph node becomes can point back
    at its evidence. Without it the report can show a topic but not why it
    thinks the meeting discussed it.
    """

    text: str
    label: str
    utterance_id: str

    def __post_init__(self) -> None:
        if self.label not in ENTITY_LABELS:
            raise ValueError(f"unknown entity label {self.label!r}; known: {ENTITY_LABELS}")


RELATION_LABELS: tuple[str, ...] = ("depends_on", "blocked_by", "part_of", "alternative_to")
"""The relations step 2 produces between two topics of one meeting.

Four, and deliberately not more. Every entry is a relation a rule can find in
spoken Korean from a marker the speaker actually said, and each one changes what
risk scoring (#35) should do with the pair:

- ``depends_on`` — the meeting said A needs B. A gap on B is a gap on A too.
- ``blocked_by`` — the meeting said B is why A cannot proceed. This is the one
  relation that is itself a finding: a blocker named out loud and never resolved
  is what the gap report exists to surface.
- ``part_of`` — B belongs to A. Two topics, one subject, so a template item
  matched by one of them is matched by the pair.
- ``alternative_to`` — the meeting weighed A against B. Symmetric, and the pair
  is a decision the meeting may or may not have closed.

``co_occurs`` is **not** here. It is not extracted from anything a speaker said;
it is what ``graph`` falls back to for a pair that shares an utterance and no
marker, and it stays in ``graph`` for that reason.
"""

SYMMETRIC_RELATIONS: frozenset[str] = frozenset({"alternative_to"})
"""Relations that hold in both directions, written as two rows.

``gap_topic_edges`` is directed and a symmetric relation is two rows rather than
a flag, so the loader never has to know which relations are which. "A 대신 B"
and "B 대신 A" are the same statement about the pair; "A는 B가 필요하다" is not.
"""


@dataclass(frozen=True)
class Relation:
    """One directed triple: ``source`` --relation--> ``target``, and where it was said.

    The two ends are the **mention texts**, not topic keys — this is the shape
    the rules produce, and mapping a mention onto the topic it belongs to is
    ``graph``'s job. Keeping it that way means a relation can be argued about
    without knowing how topics were merged.

    ``utterance_id`` is the evidence. A relation extracted from an utterance
    that analysis later drops has to be droppable with it, and a rule that
    cannot say where it fired is a rule nobody can check.
    """

    source: str
    target: str
    relation: str
    utterance_id: str

    def __post_init__(self) -> None:
        if self.relation not in RELATION_LABELS:
            raise ValueError(f"unknown relation {self.relation!r}; known: {RELATION_LABELS}")
        if not self.source or not self.target:
            raise ValueError("a relation needs both ends")


@runtime_checkable
class EntityExtractor(Protocol):
    """Named-entity extraction over a meeting's utterances.

    Takes a batch rather than one utterance: a 45-minute meeting is thousands of
    utterances, and a per-utterance call turns one pipeline pass into thousands.
    """

    @property
    def model_version(self) -> str:
        """Pinned, and recorded with the rows this produces.

        A topic graph that cannot be attributed to a model version cannot be
        compared against the next one, and gap precision is measured across
        versions (ADR 0006's shape, applied to C's own metric).
        """
        ...

    def extract(self, utterances: list[tuple[str, str]]) -> list[Entity]:
        """Entities found in ``(utterance_id, text)`` pairs.

        Returns a flat list rather than one list per utterance: every ``Entity``
        already names the utterance it came from, and the caller groups by
        entity rather than by utterance. Returning a nested list would make the
        common case — "every feature mentioned in this meeting" — a flatten at
        every call site.
        """
        ...


@runtime_checkable
class RelationExtractor(Protocol):
    """Relations between the entities already found in a meeting.

    Takes the entities rather than finding its own. A relation is between two
    things the graph has nodes for, so an implementation that extracted its own
    ends could assert a relation between two topics that do not exist — and the
    graph would either grow a node with no evidence or drop the relation
    silently.

    **This is the step ``docs/modules/gap.md`` plans to give LLM assistance**,
    and the only one: a relation needs a clause, not a whole transcript, so the
    hard cases can be sent without sending the meeting. Nothing sends anything
    today — ``relations.RuleRelations`` is the only implementation and it runs in
    this process. When an assisted one lands it goes through
    ``autune_integrations`` so ``check_outbound`` sees the request body, which
    is the rule this module's header states and PR #74 put there.
    """

    @property
    def model_version(self) -> str:
        """Pinned, and recorded with the rows this produces — as with
        ``EntityExtractor``, so a graph can be compared against the next
        version of whatever built it."""
        ...

    def extract(self, utterances: list[tuple[str, str]], entities: list[Entity]) -> list[Relation]:
        """Relations found in ``(utterance_id, text)`` pairs, given ``entities``.

        Both arguments are the whole meeting, for the reason ``extract`` on
        ``EntityExtractor`` takes a batch: a per-utterance call turns one pass
        into thousands, and an assisted implementation would make thousands of
        requests out of one.
        """
        ...
