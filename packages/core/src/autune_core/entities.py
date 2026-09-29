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
    what makes Notion, Slack and Calendar configurable per team on screen
    S28, instead of one workspace for everybody. The check constraint below
    still allows ``jira`` as a stored value -- Jira was dropped from the
    product (#82), and narrowing the constraint is a migration, a separate
    decision from this docstring.

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


class UserIntegration(Base, TimestampMixin):
    """One person's connection to one outside service -- their own calendar or
    mailbox, not the team's workspace.

    #59 kept ``team_integrations`` one row per team and said that when consent
    had to be per person, the answer was a new table rather than a wider one,
    because the two lifetimes are opposite: a team's connection outlives the
    person who made it (``connected_by`` goes ``SET NULL``), while a person's
    grant must go with them. Here the row belongs to the user and is deleted
    with them (``ON DELETE CASCADE``) -- deleting an account deletes its tokens.

    Calendar is here because each person's own tasks go on their own calendar
    and a date they move there comes back to Autune (#435); Gmail because a
    mailbox is one person's (#431). Neither is readable by a teammate or an
    admin: nothing reads a row but code acting for that user.

    Written by ``autune_core``, like ``team_integrations``; modules read it
    through ``load_user_integration``. ``secret`` is Fernet ciphertext.
    """

    __tablename__ = "user_integrations"
    __table_args__ = (
        UniqueConstraint("user_id", "service", name="uq_user_integrations_user_service"),
        CheckConstraint(
            "service IN ('calendar','gmail')",
            name="ck_user_integrations_service",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service: Mapped[str] = mapped_column(String(32), nullable=False)

    secret: Mapped[str | None] = mapped_column(Text)
    """Fernet ciphertext of the refresh token. Go through ``save_user_integration``."""

    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    """The non-secret half: which calendar, the account's address. A module's own
    sync state (a cursor, a last-seen time) goes in its own prefixed table --
    modules read this row and never write it."""


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id(USER))
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)

    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    """Google's stable subject identifier. Set on first Google sign-in and kept
    even if the account's email later changes. Null for a user who has only ever
    used a magic link. Other providers (Slack) get their own column here rather
    than a shared identities table until a third provider makes that pay off."""

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
