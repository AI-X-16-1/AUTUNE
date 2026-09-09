"""Application errors with a consistent wire format.

Never put transcript text, a file path, or personal data in an error message —
error strings reach external error tracking. See docs/architecture/privacy.md.
"""

from __future__ import annotations

from typing import Any


class AutuneError(Exception):
    """Base class. Carries a machine-readable code and safe details."""

    code = "internal_error"
    status_code = 500

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class NotFoundError(AutuneError):
    code = "not_found"
    status_code = 404

    def __init__(self, resource: str, identifier: str) -> None:
        super().__init__(f"{resource} {identifier} not found", resource=resource)


class ValidationError(AutuneError):
    code = "validation_error"
    status_code = 422

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message, **({"field": field} if field else {}))


class PermissionDeniedError(AutuneError):
    code = "permission_denied"
    status_code = 403


class PrivacyViolationError(AutuneError):
    """Raised when an operation would break a privacy constraint.

    Never caught and downgraded. If this fires, stop and fix the caller.
    """

    code = "privacy_violation"
    status_code = 500


class ConflictError(AutuneError):
    code = "conflict"
    status_code = 409


class ConfigurationError(AutuneError):
    """A deployment is missing or misconfigured a value it cannot run without.

    Raised at the point of use rather than at import, so a deployment that never
    touches the feature still starts. The message names the variable, never its
    value.
    """

    code = "configuration_error"
    status_code = 500
