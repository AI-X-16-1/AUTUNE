"""Role-pair alignment arithmetic for module E (pipeline step 4).

Pure functions over contract values — no database, no settings. The service
layer stages B's ``ExtractionResult`` and writes the result to
``intel_alignment``; this only turns each decision's ``stance_by_role`` into an
agreement score per unordered role pair.

**Input is counts per role, never per person.** ``RoleStance`` already carries
the privacy gate — at least three identified people in the role and no
unanimous role (docs/architecture/privacy.md section 3, #168) — and this module
does not re-derive a looser rule. Nothing here can see who backed or opposed a
decision, only how many people in a role did.

**How a role's position is read.** For one decision, a role's position is its
net stance over the people identified in it::

    position = (supporting - concerns) / identified        # in [-1, 1]

Dividing by ``identified`` rather than by ``supporting + concerns`` keeps the
people who said nothing in the denominator as neutral: "one of five raised a
concern" is a weaker objection than "one of one who spoke raised a concern",
and only the first reading matches what the counts actually say.

**How two roles agree.** Agreement on one decision is one minus half the
distance between the two positions, so it lands in ``[0, 1]`` — 1.0 when the
two roles lean the same way by the same amount, 0.0 when one fully backs what
the other fully opposes. A meeting's score for a pair is the mean over the
decisions both roles have a stance row on.

A decision where **neither** role expressed anything (both rows are 0/0) is
skipped for that pair: two silent roles would otherwise score a perfect 1.0,
and the heatmap would read silence as consensus.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations

from autune_contracts import Decision, RoleAlignment, RoleStance


@dataclass(frozen=True)
class PairAgreement:
    """One unordered role pair's agreement over one meeting's decisions.

    ``role_a < role_b`` always, so the same pair from two meetings lands on the
    same ``intel_alignment`` key and the heatmap averages them together.
    """

    role_a: str
    role_b: str
    score: float
    decision_count: int

    def to_contract(self) -> RoleAlignment:
        return RoleAlignment(role_a=self.role_a, role_b=self.role_b, score=self.score)


def role_position(stance: RoleStance) -> float:
    """The role's net stance on one decision, in ``[-1, 1]``."""
    return (stance.supporting - stance.concerns) / stance.identified


def _expressed(stance: RoleStance) -> bool:
    return stance.supporting + stance.concerns > 0


def decision_agreement(a: RoleStance, b: RoleStance) -> float | None:
    """How closely two roles leaned on one decision, or ``None`` when neither spoke up."""
    if not _expressed(a) and not _expressed(b):
        return None
    return 1.0 - abs(role_position(a) - role_position(b)) / 2.0


def meeting_alignment(decisions: Iterable[Decision]) -> list[PairAgreement]:
    """Per role pair, the mean agreement over the decisions both roles took a stance row on.

    Pairs that never met on a scorable decision are absent — not scored 0.5.
    Output is sorted by ``(role_a, role_b)`` so a re-aggregation is stable.
    """
    per_pair: dict[tuple[str, str], list[float]] = {}
    for decision in decisions:
        by_role = {s.role: s for s in decision.stance_by_role}
        for role_a, role_b in combinations(sorted(by_role), 2):
            agreement = decision_agreement(by_role[role_a], by_role[role_b])
            if agreement is not None:
                per_pair.setdefault((role_a, role_b), []).append(agreement)
    return [
        PairAgreement(
            role_a=role_a,
            role_b=role_b,
            score=sum(values) / len(values),
            decision_count=len(values),
        )
        for (role_a, role_b), values in sorted(per_pair.items())
    ]
