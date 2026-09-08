"""Closed value sets shared across modules.

Stored in the database as strings with a check constraint, never as PostgreSQL
enum types — see docs/architecture/data-model.md.
"""

from __future__ import annotations

from enum import StrEnum


class UtteranceKind(StrEnum):
    """The five-way classification module B applies to every utterance."""

    COMMITMENT = "commitment"
    DECISION = "decision"
    OPEN_QUESTION = "open_question"
    CONCERN = "concern"
    AMBIGUOUS = "ambiguous"


class ActionStatus(StrEnum):
    """Action item state. Maps 1:1 to the columns on the action board (S17)."""

    NEEDS_CONFIRMATION = "needs_confirmation"  # Autune-only; no external issue yet
    TODO = "todo"  # Jira: To Do
    IN_PROGRESS = "in_progress"  # Jira: In Progress
    DONE = "done"  # Jira: Done


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
    FILE_UPLOAD = "file_upload"
    DESKTOP_APP = "desktop_app"  # Phase 2


class ExternalSystem(StrEnum):
    NOTION = "notion"
    JIRA = "jira"
    SLACK = "slack"
