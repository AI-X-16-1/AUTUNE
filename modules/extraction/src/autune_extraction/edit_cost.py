"""Edit cost — the product metric ADR 0006 defines.

Pure arithmetic over counts, kept apart from the database so it can be read and
tested without one. ``service`` supplies the counts.

Two numbers, both per meeting:

- **clean acceptance rate** — the share of the model's items a person kept
  untouched. What the product is trying to move.
- **edits to acceptance** — how many corrections it took to reach a list the
  user accepted. What it costs them when the model is wrong.

Per meeting and never per person. ADR 0003 forbids per-person metrics, and
"who corrects the model most" describes one person's conduct in a meeting, which
is the same shape of data as a speaking ratio. Nothing here takes a user id
because nothing upstream records one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EditCost:
    """One meeting's correction cost. Counts only — no ids, no text."""

    model_items: int
    """Items the model proposed, including ones later deleted."""

    edited_items: int
    """Distinct model items a person changed or removed."""

    added_items: int
    """Items a person typed that the model never proposed."""

    edits: int
    """Corrections made, counting a second edit to the same item again."""

    @property
    def clean_acceptance_rate(self) -> float:
        """Share of the model's items kept untouched.

        Zero model items returns 1.0 rather than 0.0. A meeting the model found
        nothing in has no items to get wrong, and scoring it as a total failure
        would drag the average down for meetings that were never a test — a
        short stand-up with no commitments is not the model missing anything.
        The count travels alongside so an average can be weighted.
        """
        if self.model_items == 0:
            return 1.0
        return (self.model_items - self.edited_items) / self.model_items

    @property
    def edits_to_acceptance(self) -> int:
        """Corrections it took to reach the list the user accepted.

        Additions count. An item the model missed costs the user more than one
        it got wrong — they have to notice the absence, which is the failure
        recall makes likely and the one editing cannot fix by itself.
        """
        return self.edits

    @property
    def is_untouched(self) -> bool:
        """The user changed nothing at all. The number this metric aims at."""
        return self.edits == 0 and self.added_items == 0


def summarise(kinds: list[str]) -> dict[str, int]:
    """Count edit-event kinds. Unknown kinds are counted, not dropped.

    A kind this function does not know about is a schema change that outran the
    metric, and silently ignoring it would understate the cost.
    """
    counts: dict[str, int] = {}
    for kind in kinds:
        counts[kind] = counts.get(kind, 0) + 1
    return counts
