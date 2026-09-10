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

ENTITY_LABELS: tuple[str, ...] = ("feature", "system", "metric", "person", "date")
"""What module C looks for. ``docs/modules/gap.md`` step 1 names these five.

Deliberately not spaCy's own inventory: a model trained on news text emits
``ORG``, ``LOC`` and ``DATE``, and a meeting about a search ranking has no
"organisations" in it worth graphing. Each implementation maps its own labels
onto these, so the topic graph does not change shape when the model does.
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
