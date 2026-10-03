"""Google sign-in over OpenID Connect.

Authorization Code flow, confidential client:

1. ``authorization_url`` sends the browser to Google with a ``state`` and a
   ``nonce``.
2. Google redirects back with a ``code``; ``exchange_code`` trades it, over a
   server-to-server call, for an ID token.
3. ``verify`` checks that ID token's signature against Google's JWKS and its
   ``aud`` / ``iss`` / ``exp`` / ``nonce`` claims, then hands back the identity.

No Google access token is kept — sign-in needs the ID token and nothing else.

**Calendar is a second request** (#435): a signed-in person asks for
``calendar.events`` with ``access_type=offline``, and the refresh token Google
returns is theirs, stored in ``user_integrations``. It goes to the deployment's
integration client when one is set (``get_google_integration_client``) and to
the sign-in client otherwise -- the same redirect URI either way, since one
callback finishes both. **Sending mail as the person** (``gmail.send``, #552)
is a third request of the same shape, its grant stored apart from the
calendar's.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from autune_core.errors import AutuneError, PermissionDeniedError
from autune_core.logging import get_logger
from autune_core.settings import get_settings

log = get_logger(__name__)

CLOCK_SKEW_SECONDS = 60
"""How far this server's clock may sit from Google's when an ID token's
``iat`` and ``exp`` are checked. With none, a clock one second behind read a
token Google had just issued as not yet valid and refused the sign-in (#618,
found in the 2026-10-01 real-service check on a PC 0.9 s behind)."""

# RFC 6749 section 5.2 error codes are lowercase ASCII with underscores.
_OAUTH_ERROR = re.compile(r"[a-z_]{1,64}")

AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
VALID_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
SCOPE = "openid email profile"

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
"""Read and write events -- a person's own due dates on their primary calendar.
Not ``calendar``: nothing here manages calendars or sharing."""

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
"""Send mail as the person -- an invitation link from their own address (#552).
Sends only: nothing in a mailbox can be read with it. A sensitive scope, not
a restricted one, unlike reading a mailbox (#431)."""

REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"


class GoogleGrant:
    """What a code exchange returns that we act on."""

    __slots__ = ("id_token", "refresh_token", "scopes")

    def __init__(self, id_token: str, refresh_token: str | None, scopes: frozenset[str]) -> None:
        self.id_token = id_token
        self.refresh_token = refresh_token
        self.scopes = scopes


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

    @property
    def client_id(self) -> str:
        """Which OAuth client this is. Public by nature -- it is in every consent
        URL -- and kept beside a stored grant so that a grant issued to another
        client can be told apart without asking Google."""
        return self._client_id

    def authorization_url(
        self, *, state: str, nonce: str, scope: str = SCOPE, offline: bool = False
    ) -> str:
        """Sign-in by default. ``offline`` asks for a refresh token as well:
        ``prompt=consent`` because Google hands one out only on a consent screen,
        and ``include_granted_scopes`` so connecting a calendar keeps sign-in's
        scopes rather than replacing them."""
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": scope,
            "state": state,
            "nonce": nonce,
            "access_type": "offline" if offline else "online",
            "prompt": "consent" if offline else "select_account",
        }
        if offline:
            params["include_granted_scopes"] = "true"
        return f"{AUTHORIZE_ENDPOINT}?{urlencode(params)}"

    def exchange_code(self, code: str) -> str:
        """Trade an authorization code for an ID token (a signed JWT string)."""
        return self.exchange_grant(code).id_token

    def exchange_grant(self, code: str) -> GoogleGrant:
        """Trade an authorization code for everything the exchange returns that we
        use: the ID token, a refresh token when one was asked for, and the scopes
        the person actually granted -- Google lets them untick one."""
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
            # error string, which reaches error tracking. Log only Google's error
            # code -- ``invalid_client`` (wrong secret), ``invalid_grant`` (spent or
            # expired code), ``redirect_uri_mismatch`` -- which names the setting to
            # fix without repeating anything from the request.
            log.warning(
                "auth_google_token_rejected",
                status=response.status_code,
                error=_oauth_error(response),
            )
            raise PermissionDeniedError("Google rejected the authorization code")

        body = response.json()
        id_token = body.get("id_token")
        if not id_token:
            raise PermissionDeniedError("Google response carried no ID token")
        refresh = body.get("refresh_token")
        return GoogleGrant(
            id_token=str(id_token),
            refresh_token=str(refresh) if refresh else None,
            scopes=frozenset(str(body.get("scope", "")).split()),
        )

    def revoke(self, token: str) -> bool:
        """Revoke a grant at Google. ``False`` when Google could not be reached or
        refused -- a disconnect removes our copy either way, and says so."""
        try:
            response = self._http.post(REVOKE_ENDPOINT, data={"token": token})
        except httpx.HTTPError:
            return False
        return response.status_code == httpx.codes.OK

    def verify(self, id_token: str, *, nonce: str) -> GoogleIdentity:
        """Who signed in: a verified ID token that also names an email -- the
        sign-in path upserts the user by it."""
        claims = self.verify_request(id_token, nonce=nonce)
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

    def verify_request(self, id_token: str, *, nonce: str) -> dict[str, Any]:
        """That this ID token answers *our* request: signature, audience,
        issuer, expiry and nonce. It asks nothing about the account, so it
        holds for a grant without the ``email`` scope -- the calendar connect,
        which may be on another Google account than the one that signed in
        (#452 review)."""
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(id_token)
            claims: dict[str, Any] = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                options={"require": ["exp", "iat", "aud", "iss", "sub"]},
                leeway=CLOCK_SKEW_SECONDS,
            )
        except jwt.InvalidTokenError as exc:
            # Which check refused it, and nothing of the token: its claims carry
            # the person's email and Google account id (#618).
            log.warning("auth_google_id_token_rejected", reason=type(exc).__name__)
            raise PermissionDeniedError("Google ID token failed verification") from exc

        if claims.get("iss") not in VALID_ISSUERS:
            raise PermissionDeniedError("Google ID token has an unexpected issuer")
        if claims.get("nonce") != nonce:
            raise PermissionDeniedError("Google ID token nonce does not match the request")
        return claims


def _oauth_error(response: httpx.Response) -> str:
    """Google's ``error`` field if it is a well-formed OAuth error code, else a
    placeholder: anything else in that field is not safe to log verbatim."""
    try:
        error = response.json().get("error")
    except ValueError:
        return "unparseable"
    if isinstance(error, str) and _OAUTH_ERROR.fullmatch(error):
        return error
    return "unrecognised"


@lru_cache
def get_google_client() -> GoogleOAuthClient:
    settings = get_settings()
    return GoogleOAuthClient(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        redirect_uri=settings.google_redirect_uri,
    )


@lru_cache
def get_google_integration_client() -> GoogleOAuthClient | None:
    """The client a person's calendar is connected with, when the deployment has
    a second one for that (``Settings.google_integration_client_id``). ``None``
    when it has not: the caller then uses the sign-in client, which is what
    every deployment did before the second one existed. The redirect URI is
    the sign-in client's -- one callback finishes both flows."""
    settings = get_settings()
    if not settings.google_integration_configured:
        return None
    return GoogleOAuthClient(
        client_id=settings.google_integration_client_id,
        client_secret=settings.google_integration_client_secret,
        redirect_uri=settings.google_redirect_uri,
    )
