"""Closed value sets shared across modules.

Stored in the database as strings with a check constraint, never as PostgreSQL
enum types — see docs/architecture/data-model.md.
"""

from __future__ import annotations

from enum import StrEnum


class UtteranceKind(StrEnum):
    """The kinds module B reports for an utterance.

    B's classifier also answers ``none`` — most of a meeting is none of these —
    and a ``none`` utterance never leaves the module: it is absent from
    ``ExtractionResult.classifications`` rather than listed with a sixth kind.
    """

    COMMITMENT = "commitment"
    DECISION = "decision"
    OPEN_QUESTION = "open_question"
    CONCERN = "concern"
    AMBIGUOUS = "ambiguous"


class ActionStatus(StrEnum):
    """Action item state. Maps 1:1 to the columns on the action board (S17)."""

    NEEDS_CONFIRMATION = "needs_confirmation"  # Autune-only; no external issue yet
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class GapSeverity(StrEnum):
    HIGH = "high"  # risk_score >= 0.7; the only level surfaced by default
    MEDIUM = "medium"  # 0.5 - 0.7
    LOW = "low"


class ChangeType(StrEnum):
    """How a decision moved between meetings."""

    UNCHANGED = "unchanged"
    MODIFIED = "modified"
    REVERSED = "reversed"
    NEW = "new"


class NliLabel(StrEnum):
    ENTAILMENT = "entailment"
    CONTRADICTION = "contradiction"
    NEUTRAL = "neutral"


class TranscriptSource(StrEnum):
    """How the audio reached us. Both MVP paths converge on one TranscriptReady."""

    FILE_UPLOAD = "file_upload"
    WEB_MIC = "web_mic"  # Live browser recording (S06, S10, S13)
    DESKTOP_APP = "desktop_app"  # Phase 2


class ExternalSystem(StrEnum):
    NOTION = "notion"
    JIRA = "jira"
    """Unused -- Jira was evaluated and dropped from the product (#82,
    2026-09-10): both its non-app auth paths are tied to a person's identity,
    and there was no path to a person-independent one (Forge/Connect) inside
    this project's six weeks. Kept rather than removed: invariant 5 makes
    dropping a contract value a breaking change, and no producer has ever
    emitted it."""
    SLACK = "slack"
