"""Shared infrastructure: settings, logging, database, errors, identifiers.

Everything here is used by all five modules, so it changes rarely and by team
agreement. ``autune_core`` never imports a module.
"""

from . import deletion, ids
from .auth import (
    SESSION_COOKIE,
    CurrentUser,
    clear_session_cookie,
    current_user,
    issue_token,
    require_self,
    set_session_cookie,
)
from .auth_service import upsert_user_from_google
from .db import Base, get_engine, get_session, get_sessionmaker, session_scope
from .entities import Meeting, Participant, Team, TeamMember, User, Utterance
from .errors import (
    AutuneError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    PrivacyViolationError,
    ValidationError,
)
from .ids import new_id
from .logging import configure_logging, get_logger
from .settings import Settings, get_settings

__all__ = [
    "Settings",
    "get_settings",
    "configure_logging",
    "get_logger",
    "Base",
    "Meeting",
    "Participant",
    "Team",
    "TeamMember",
    "User",
    "Utterance",
    "CurrentUser",
    "current_user",
    "issue_token",
    "require_self",
    "SESSION_COOKIE",
    "set_session_cookie",
    "clear_session_cookie",
    "upsert_user_from_google",
    "deletion",
    "get_engine",
    "get_sessionmaker",
    "get_session",
    "session_scope",
    "AutuneError",
    "NotFoundError",
    "ValidationError",
    "PermissionDeniedError",
    "PrivacyViolationError",
    "ConflictError",
    "ids",
    "new_id",
]
