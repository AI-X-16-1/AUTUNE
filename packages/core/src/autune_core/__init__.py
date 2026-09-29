"""Shared infrastructure: settings, logging, database, errors, identifiers.

Everything here is used by all five modules, so it changes rarely and by team
agreement. ``autune_core`` never imports a module.
"""

from . import crypto, deletion, ids
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
from .entities import (
    Meeting,
    Participant,
    Team,
    TeamIntegration,
    TeamMember,
    User,
    UserIntegration,
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
from .events import consumer_task_suffix, publish, subscribers
from .ids import new_id
from .integrations_config import (
    SERVICES,
    IntegrationConfig,
    disconnect_integration,
    load_integration,
    require_integration,
    save_integration,
)
from .jira_connection import JiraAccess, jira_access
from .logging import configure_logging, get_logger
from .periodic import beat_schedule, is_periodic_task_name, periodic
from .settings import Settings, get_settings
from .user_integrations import (
    USER_SERVICES,
    UserIntegrationConfig,
    disconnect_user_integration,
    load_user_integration,
    save_user_integration,
    users_with_integration,
)

__all__ = [
    "Settings",
    "get_settings",
    "configure_logging",
    "get_logger",
    "publish",
    "subscribers",
    "consumer_task_suffix",
    "periodic",
    "beat_schedule",
    "is_periodic_task_name",
    "Base",
    "Meeting",
    "Participant",
    "Team",
    "TeamIntegration",
    "JiraAccess",
    "jira_access",
    "UserIntegration",
    "USER_SERVICES",
    "UserIntegrationConfig",
    "load_user_integration",
    "save_user_integration",
    "disconnect_user_integration",
    "users_with_integration",
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
