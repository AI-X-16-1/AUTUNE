"""Integration failures, separated by whether retrying could help."""

from __future__ import annotations

from autune_core.errors import AutuneError


class IntegrationError(AutuneError):
    code = "integration_error"
    status_code = 502


class TransientIntegrationError(IntegrationError):
    """Network blip, rate limit, 5xx. Celery should retry these."""

    code = "integration_unavailable"


class PermanentIntegrationError(IntegrationError):
    """Bad credentials, missing resource, malformed request. Retrying will not help."""

    code = "integration_rejected"
