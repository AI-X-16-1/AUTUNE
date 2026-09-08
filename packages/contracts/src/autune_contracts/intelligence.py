"""E -> apps. Team-level aggregates only.

Speaking ratios are not in this payload and never will be. They are computed,
delivered to the speaker by DM, and discarded. See docs/architecture/privacy.md
section 3.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ._base import ContractModel, Payload

Grade = Literal["A", "B", "C", "D", "E", "F"]


class QualityScore(ContractModel):
    grade: Grade
    value: float = Field(ge=0, le=1)


class RoleAlignment(ContractModel):
    """Agreement between two roles. Role level, never person level."""

    role_a: str
    role_b: str
    score: float = Field(ge=0, le=1)


class Prediction(ContractModel):
    kind: str
    horizon_days: int = Field(gt=0)
    probability: float = Field(ge=0, le=1)


class IntelligenceSnapshot(Payload):
    team_id: str = Field(pattern=r"^team_")
    quality_score: QualityScore
    gap_distribution: dict[str, int] = Field(default_factory=dict)
    alignment: list[RoleAlignment] = Field(default_factory=list)
    predictions: list[Prediction] = Field(default_factory=list)
    missing_sources: list[str] = Field(
        default_factory=list,
        description="Modules that had not reported when this snapshot was built.",
    )
