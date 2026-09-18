"""Shared entities.

Module A writes ``meetings``, ``participants`` and ``utterances``. B, C, D and E
read them and never issue INSERT, UPDATE or DELETE — put derived state in your
own prefixed tables with a foreign key back here.

The absence of a table prefix is what marks a table as shared.
See docs/architecture/data-model.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .ids import MEETING, PARTICIPANT, TEAM, USER, UTTERANCE, new_id


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id(TEAM))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    """Per-team override of the retention window. See docs/architecture/privacy.md."""

    members: Mapped[list[TeamMember]] = relationship(back_populates="team")


class TeamIntegration(Base, TimestampMixin):
    """One team's connection to one outside service.

    Credentials belong to the customer team, not to the deployment. A row here is
    what makes Notion, Jira, Slack and Calendar configurable per team on screen
    S28, instead of one workspace for everybody.

    Written by ``autune_core``, the way ``users`` and ``teams`` are — no module
    writes it. Modules read it through ``load_integration``.

    ``secret`` is Fernet ciphertext, never a usable token. ``config`` holds the
    non-secret half — database ids, project key, property mapping — which is
    JSONB because its shape differs per service and nothing joins on it.
    """

    __tablename__ = "team_integrations"
    __table_args__ = (
        UniqueConstraint("team_id", "service", name="uq_team_integrations_team_service"),
        CheckConstraint(
            "service IN ('notion','jira','slack','calendar')",
            name="ck_team_integrations_service",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service: Mapped[str] = mapped_column(String(32), nullable=False)

    secret: Mapped[str | None] = mapped_column(Text)
    """Fernet ciphertext. Never assign a plaintext token to this column —
    go through ``save_integration``."""

    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    connected_by: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL")
    )
    """Who connected it, for the sync-log drawer on S28. Null once they leave."""


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id(USER))
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)

    memberships: Mapped[list[TeamMember]] = relationship(back_populates="user")


class TeamMember(Base, TimestampMixin):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str | None] = mapped_column(String(50))
    """Job role — PM, Dev, Design, Data. Drives role-level analytics only."""

    team: Mapped[Team] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class Meeting(Base, TimestampMixin):
    """One analysis unit: a recording and everything derived from it."""

    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled','recording','analyzing','awaiting_confirmation',"
            "'complete','delivered','failed')",
            name="ck_meetings_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id(MEETING))
    team_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="file_upload")
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="ko")

    original_audio_deleted: Mapped[bool] = mapped_column(nullable=False, default=False)
    """Set once the recording is gone. Downstream refuses to process while False."""
    pii_masked: Mapped[bool] = mapped_column(nullable=False, default=False)

    is_backfill: Mapped[bool] = mapped_column(nullable=False, default=False)
    """True for a historical recording uploaded well after the meeting itself
    happened, as opposed to a normal live-or-near-live upload. Written only by
    module A, on ingestion -- **schema only in this PR**; no module writes a
    non-default value here yet.

    Exists so a downstream module can tell "this decision just changed" from
    "this decision changed months ago and someone bulk-imported the recording
    today" -- module D hits exactly that ambiguity for its decision-drift
    Slack notice (see docs/modules/context.md and issue #257). Module A's
    upload flow still needs to decide *how* this gets set (an explicit
    upload-time choice? inferred from a large gap between `started_at` and
    `created_at`?) before it carries a real value -- that is a design
    conversation for module A's owner, not something this PR settles."""

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    """When the retention sweep deletes this meeting and everything derived."""

    participants: Mapped[list[Participant]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan"
    )
    utterances: Mapped[list[Utterance]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan"
    )


class Participant(Base, TimestampMixin):
    """Someone present at a meeting. May never resolve to a user account."""

    __tablename__ = "participants"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id(PARTICIPANT)
    )
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    speaker_label: Mapped[str] = mapped_column(String(100), nullable=False)
    """'Speaker 2' until identification succeeds, then the person's name."""
    role: Mapped[str | None] = mapped_column(String(50))
    consented: Mapped[bool] = mapped_column(nullable=False, default=False)
    """False excludes this person's speech from analysis entirely."""

    meeting: Mapped[Meeting] = relationship(back_populates="participants")


class Utterance(Base, TimestampMixin):
    """One continuous stretch of speech. ``text`` is always PII-masked.

    There is no unmasked column, and there will not be one. Masking happens in
    module A before the first write. See docs/architecture/privacy.md section 2.
    """

    __tablename__ = "utterances"
    __table_args__ = (
        Index("ix_utterances_meeting_start", "meeting_id", "start_sec"),
        CheckConstraint("end_sec >= start_sec", name="ck_utterances_time_order"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id(UTTERANCE))
    meeting_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    participant_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("participants.id", ondelete="SET NULL"), index=True
    )
    speaker_label: Mapped[str] = mapped_column(String(100), nullable=False)
    start_sec: Mapped[float] = mapped_column(Float, nullable=False)
    end_sec: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    meeting: Mapped[Meeting] = relationship(back_populates="utterances")
