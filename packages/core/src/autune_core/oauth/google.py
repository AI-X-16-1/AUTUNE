"""Google sign-in over OpenID Connect.

Authorization Code flow, confidential client:

1. ``authorization_url`` sends the browser to Google with a ``state`` and a
   ``nonce``.
2. Google redirects back with a ``code``; ``exchange_code`` trades it, over a
   server-to-server call, for an ID token.
3. ``verify`` checks that ID token's signature against Google's JWKS and its
   ``aud`` / ``iss`` / ``exp`` / ``nonce`` claims, then hands back the identity.

No Google access token is kept — sign-in needs the ID token and nothing else.
Calendar access (module D, Phase 2) is a separate grant with its own storage.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from autune_core.errors import AutuneError, PermissionDeniedError
from autune_core.settings import get_settings

AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
VALID_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
SCOPE = "openid email profile"


class GoogleIdentity:
    """The bits of a verified Google ID token that we act on."""

    __slots__ = ("sub", "email", "email_verified", "name", "picture")

    def __init__(
        self,
        sub: str,
        email: str,
        email_verified: bool,
        name: str | None,
        picture: str | None,
    ) -> None:
        self.sub = sub
        self.email = email
        self.email_verified = email_verified
        self.name = name
        self.picture = picture


class GoogleOAuthClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http: httpx.Client | None = None,
        jwks_client: jwt.PyJWKClient | None = None,
    ) -> None:
        if not (client_id and client_secret and redirect_uri):
            raise AutuneError("Google sign-in is not configured on this server")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = http or httpx.Client(timeout=10.0)
        self._jwks_client = jwks_client or jwt.PyJWKClient(JWKS_URI)

    def authorization_url(self, *, state: str, nonce: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "state": state,
                "nonce": nonce,
                "access_type": "online",
                "prompt": "select_account",
            }
        )
        return f"{AUTHORIZE_ENDPOINT}?{query}"

    def exchange_code(self, code: str) -> str:
        """Trade an authorization code for an ID token (a signed JWT string)."""
        try:
            response = self._http.post(
                TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": self._redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise AutuneError("could not reach Google to complete sign-in") from exc

        if response.status_code != httpx.codes.OK:
            # The body can carry the reason but also the code; keep it out of the
            # error string, which reaches error tracking.
            raise PermissionDeniedError("Google rejected the authorization code")

        id_token = response.json().get("id_token")
        if not id_token:
            raise PermissionDeniedError("Google response carried no ID token")
        return str(id_token)

    def verify(self, id_token: str, *, nonce: str) -> GoogleIdentity:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(id_token)
            claims: dict[str, Any] = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                options={"require": ["exp", "iat", "aud", "iss", "sub"]},
            )
        except jwt.InvalidTokenError as exc:
            raise PermissionDeniedError("Google ID token failed verification") from exc

        if claims.get("iss") not in VALID_ISSUERS:
            raise PermissionDeniedError("Google ID token has an unexpected issuer")
        if claims.get("nonce") != nonce:
            raise PermissionDeniedError("Google ID token nonce does not match the request")

        email = claims.get("email")
        if not email:
            raise PermissionDeniedError("Google account exposes no email address")

        return GoogleIdentity(
            sub=str(claims["sub"]),
            email=str(email),
            email_verified=bool(claims.get("email_verified", False)),
            name=claims.get("name"),
            picture=claims.get("picture"),
        )


@lru_cache
def get_google_client() -> GoogleOAuthClient:
    settings = get_settings()
    return GoogleOAuthClient(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        redirect_uri=settings.google_redirect_uri,
    )
