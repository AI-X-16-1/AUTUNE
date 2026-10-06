"""Shared base for every cross-module payload.

Contracts are pure shapes: no behavior, no I/O, no database access, and no
imports from ``autune_core`` or any module. See docs/architecture/contracts.md.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "2.5"
"""Bump the minor for an additive change, the major for a breaking one.

A breaking change needs a Slack announcement and approval from every affected
module owner before it merges.
"""


class ContractModel(BaseModel):
    """Base for every contract type.

    ``extra="ignore"`` is deliberate. A consumer pinned to an older minor
    version must keep working when a producer adds an optional field; forbidding
    extras would turn every additive change into a breaking one.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)


class Payload(ContractModel):
    """A payload that crosses a module boundary."""

    contract_version: str = Field(default=CONTRACT_VERSION)
    meeting_id: str = Field(pattern=r"^mtg_")


class TeamPayload(ContractModel):
    """A payload about a team rather than one meeting.

    ``Payload`` requires a ``meeting_id``, and some state belongs to no single
    meeting -- the issues a team has open (#436). Same version rule.
    """

    contract_version: str = Field(default=CONTRACT_VERSION)
    team_id: str = Field(pattern=r"^team_")


def major(version: str) -> str:
    return version.split(".", 1)[0]


def validate_major_version(payload: Payload | TeamPayload) -> None:
    """Reject a payload from an incompatible producer, loudly.

    Consumers call this before acting on a payload. Guessing at a mismatched
    major version is how four modules break quietly.
    """
    if major(payload.contract_version) != major(CONTRACT_VERSION):
        raise ValueError(
            f"contract major version mismatch: payload is "
            f"{payload.contract_version}, this build expects {CONTRACT_VERSION}"
        )
