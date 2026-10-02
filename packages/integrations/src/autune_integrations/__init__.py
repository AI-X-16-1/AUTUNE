"""Wrappers for the services Autune talks to.

Every module notifies through these clients rather than calling an API
directly, so retries, error handling and — most importantly — the outbound
privacy checks live in one place instead of five.

W1 defines the boundary and the guards. The full API surface is filled in
during W3 by the owners who need it: extraction for Notion and Jira; Calendar
by the Workload subagent's owner (#260, #435).
"""

from . import fakes, privacy
from .calendar import (
    CalendarClient,
    CalendarEvent,
    ReconnectRequiredError,
    refresh_access_token,
)
from .errors import (
    IntegrationError,
    PermanentIntegrationError,
    TransientIntegrationError,
)
from .jira import JiraClient
from .notion import NotionClient
from .privacy import (
    assert_masked,
    assert_personal_delivery,
    check_outbound,
    find_unmasked,
)
from .slack import PostedMessage, SlackApi, SlackClient

__all__ = [
    "PostedMessage",
    "SlackClient",
    "SlackApi",
    "NotionClient",
    "JiraClient",
    "CalendarClient",
    "CalendarEvent",
    "ReconnectRequiredError",
    "refresh_access_token",
    "IntegrationError",
    "TransientIntegrationError",
    "PermanentIntegrationError",
    "privacy",
    "check_outbound",
    "assert_masked",
    "assert_personal_delivery",
    "find_unmasked",
    "fakes",
]
