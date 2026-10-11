"""Application errors with a consistent wire format.

Never put transcript text, a file path, or personal data in an error message —
error strings reach external error tracking. See docs/architecture/privacy.md.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
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


class SchemaValidationError(AutuneError):
    """A request that failed its schema, in the shared wire format (#1225).

    FastAPI's default 422 answers ``{"detail": [...]}``, and every entry carries
    ``input`` -- the value that was sent, a typed RRN or phone number included
    -- and often ``ctx``. This keeps where the error is (``loc``), what kind it
    is (``type``) and pydantic's message (``msg``), so the refusal names the
    field and never repeats the value. ``apps/api`` renders it for every
    ``RequestValidationError``.

    A message is pydantic's or a validator's own text. Should one ever quote
    the submitted string, the message is replaced too: the rule is "never the
    value", not "never the ``input`` key".
    """

    code = "validation_error"
    status_code = 422

    def __init__(self, errors: Iterable[Mapping[str, Any]]) -> None:
        super().__init__(
            "Request validation failed", errors=[_without_input(error) for error in errors]
        )


def _without_input(error: Mapping[str, Any]) -> dict[str, Any]:
    sent = error.get("input")
    message = str(error.get("msg", ""))
    if isinstance(sent, str) and sent and sent in message:
        message = "Invalid value"
    return {
        "loc": [part if isinstance(part, int) else str(part) for part in error.get("loc", ())],
        "type": str(error.get("type", "")),
        "msg": message,
    }


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
