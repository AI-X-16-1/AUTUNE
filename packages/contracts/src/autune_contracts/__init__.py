"""Data contracts crossing module boundaries.

Frozen after W1: additive changes only. Removing or renaming a field, changing a
type, or tightening a constraint is breaking — announce it in Slack, get approval
from every affected module owner, and bump the version in the same pull request.

See docs/architecture/contracts.md.
"""

from . import fixtures
from ._base import CONTRACT_VERSION, ContractModel, Payload, validate_major_version
from .context import ContextLinks, DecisionChange, TopicLink
from .enums import (
    ActionStatus,
    ChangeType,
    ExternalSystem,
    GapSeverity,
    NliLabel,
    TranscriptSource,
    UtteranceKind,
)
from .events import (
    CONTEXT_COMPLETED,
    EVENTS,
    EXTRACTION_COMPLETED,
    GAP_COMPLETED,
    INTELLIGENCE_COMPLETED,
    MODULES,
    TRANSCRIPT_READY,
)
from .extraction import (
    ActionItem,
    AmbiguousAgreement,
    Classification,
    ExternalRef,
    ExtractionResult,
)
from .gap import Gap, GapReport, Participation, Topic
from .intelligence import (
    IntelligenceSnapshot,
    Prediction,
    QualityScore,
    RoleAlignment,
)
from .transcript import PrivacyFlags, TranscriptMetadata, TranscriptReady, Utterance

__all__ = [
    "CONTRACT_VERSION",
    "fixtures",
    "ContractModel",
    "Payload",
    "validate_major_version",
    # events
    "TRANSCRIPT_READY",
    "EXTRACTION_COMPLETED",
    "EVENTS",
    "GAP_COMPLETED",
    "CONTEXT_COMPLETED",
    "INTELLIGENCE_COMPLETED",
    "MODULES",
    # enums
    "UtteranceKind",
    "ActionStatus",
    "GapSeverity",
    "ChangeType",
    "NliLabel",
    "TranscriptSource",
    "ExternalSystem",
    # A -> B, C, D
    "TranscriptReady",
    "Utterance",
    "TranscriptMetadata",
    "PrivacyFlags",
    # B -> E
    "ExtractionResult",
    "ActionItem",
    "Classification",
    "Decision",
    "AmbiguousAgreement",
    "ExternalRef",
    # C -> E
    "GapReport",
    "Gap",
    "Topic",
    "Participation",
    # D -> E
    "ContextLinks",
    "TopicLink",
    "DecisionChange",
    # E -> apps
    "IntelligenceSnapshot",
    "QualityScore",
    "RoleAlignment",
    "Prediction",
]
