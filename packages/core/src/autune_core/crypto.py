"""Symmetric encryption for secrets held at rest.

Integration credentials belong to a customer team, not to us. They sit in
`team_integrations` rows, so they are encrypted before they reach the column and
decrypted only when a client is about to make a call.

One key for the whole deployment, from ``AUTUNE_ENCRYPTION_KEY``. Per-team keys
would need a key store, which is a fourth piece of infrastructure and an ADR —
see docs/architecture/data-model.md.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .errors import ConfigurationError
from .settings import get_settings


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().encryption_key
    if not key:
        raise ConfigurationError(
            "AUTUNE_ENCRYPTION_KEY is not set; integration secrets cannot be read or "
            'written. Generate one: python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise ConfigurationError(
            "AUTUNE_ENCRYPTION_KEY is not a valid Fernet key (32 url-safe base64 bytes)"
        ) from exc


def encrypt(value: str) -> str:
    """Encrypt a secret for storage. The result is safe to put in a text column."""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Reverse of :func:`encrypt`.

    A failure here means the key changed or the row was written by a different
    deployment. The message never includes the ciphertext.
    """
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ConfigurationError(
            "an integration secret could not be decrypted; AUTUNE_ENCRYPTION_KEY does "
            "not match the key it was written with"
        ) from exc
