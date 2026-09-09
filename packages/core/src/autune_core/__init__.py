"""Shared infrastructure: settings, logging, database, errors, identifiers.

Everything here is used by all five modules, so it changes rarely and by team
agreement. ``autune_core`` never imports a module.
"""

from . import crypto, deletion, ids
from .auth import CurrentUser, current_user, issue_token, require_self
from .db import Base, get_engine, get_session, get_sessionmaker, session_scope
from .entities import (
    Meeting,
    Participant,
    Team,
    TeamIntegration,
    TeamMember,
    User,
    Utterance,
)
from .errors import (
    AutuneError,
    ConfigurationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    PrivacyViolationError,
    ValidationError,
)
from .ids import new_id
from .integrations_config import (
    SERVICES,
    IntegrationConfig,
    disconnect_integration,
    load_integration,
    require_integration,
    save_integration,
)
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
    "TeamIntegration",
    "TeamMember",
    "User",
    "Utterance",
    "CurrentUser",
    "current_user",
    "issue_token",
    "require_self",
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
    "ConfigurationError",
    "ids",
    "new_id",
    "crypto",
    "SERVICES",
    "IntegrationConfig",
    "load_integration",
    "require_integration",
    "save_integration",
    "disconnect_integration",
]
