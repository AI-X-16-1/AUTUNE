"""Jira Cloud over Atlassian's OAuth 2.0 (3LO) -- a team connects its Jira site
with one click (#82, #428).

Authorization Code flow, confidential client:

1. ``authorization_url`` sends the browser to Atlassian's consent screen with a
   ``state`` (the same browser-bound state as Google sign-in).
2. ``exchange_code`` trades the code for an access token and a **rotating**
   refresh token -- ``offline_access`` must be among the scopes or there is no
   refresh token at all.
3. ``sites`` lists the Jira sites the grant reaches; each has the ``cloud_id``
   every API call is addressed by (``api.atlassian.com/ex/jira/{cloud_id}``).

**The grant is a person's** (#82): Atlassian ties it to whoever consented, and a
password change or 90 days unused ends it. The product accepts that knowingly
and owes a visible "reconnect" (``needs_reconnect`` in the team's config,
``jira_access``) rather than a silent failure.

**Refresh tokens rotate**: every refresh returns a new one and the old one stops
working. ``autune_core.jira_connection.jira_access`` is therefore the only
place that refreshes, and it saves the new token in its own transaction before
anything else can fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

import httpx

from autune_core.errors import AutuneError, PermissionDeniedError
from autune_core.settings import get_settings

AUTHORIZE_ENDPOINT = "https://auth.atlassian.com/authorize"
TOKEN_ENDPOINT = "https://auth.atlassian.com/oauth/token"
RESOURCES_ENDPOINT = "https://api.atlassian.com/oauth/token/accessible-resources"
API_ROOT = "https://api.atlassian.com/ex/jira"


class JiraReconnectRequiredError(AutuneError):
    """Atlassian refused the stored refresh token -- revoked, expired after 90
    days unused, or its person's password changed (#82). A person must connect
    again; nothing retries its way out."""

    code = "jira_reconnect_required"
    status_code = 409


@dataclass(frozen=True)
class AtlassianTokens:
    access_token: str
    refresh_token: str | None
    scopes: frozenset[str]


@dataclass(frozen=True)
class JiraSite:
    cloud_id: str
    url: str
    name: str


@dataclass(frozen=True)
class JiraProject:
    key: str
    name: str


class AtlassianOAuthClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: str,
        http: httpx.Client | None = None,
    ) -> None:
        if not (client_id and client_secret and redirect_uri):
            raise AutuneError("Jira connect is not configured on this server")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._scopes = scopes
        self._http = http or httpx.Client(timeout=10.0)

    def authorization_url(self, *, state: str) -> str:
        query = urlencode(
            {
                "audience": "api.atlassian.com",
                "client_id": self._client_id,
                "scope": self._scopes,
                "redirect_uri": self._redirect_uri,
                "state": state,
                "response_type": "code",
                "prompt": "consent",
            }
        )
        return f"{AUTHORIZE_ENDPOINT}?{query}"

    def exchange_code(self, code: str) -> AtlassianTokens:
        return self._token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
            },
            refused=PermissionDeniedError("Atlassian rejected the authorization code"),
        )

    def refresh(self, refresh_token: str) -> AtlassianTokens:
        return self._token(
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
            refused=JiraReconnectRequiredError("Atlassian refused the refresh token"),
        )

    def _token(self, body: dict[str, str], *, refused: AutuneError) -> AtlassianTokens:
        payload = {**body, "client_id": self._client_id, "client_secret": self._client_secret}
        try:
            response = self._http.post(TOKEN_ENDPOINT, json=payload)
        except httpx.HTTPError as exc:
            raise AutuneError("could not reach Atlassian") from exc
        if response.status_code >= 500:
            raise AutuneError(f"Atlassian answered {response.status_code}")
        if response.status_code >= 400:
            # The body may echo the code or token; it stays out of the message.
            raise refused
        data = response.json()
        refresh = data.get("refresh_token")
        return AtlassianTokens(
            access_token=str(data["access_token"]),
            refresh_token=str(refresh) if refresh else None,
            scopes=frozenset(str(data.get("scope", "")).split()),
        )

    def sites(self, access_token: str) -> list[JiraSite]:
        """The Jira sites this grant reaches -- usually one."""
        body = self._get(RESOURCES_ENDPOINT, access_token)
        return [
            JiraSite(cloud_id=str(s["id"]), url=str(s.get("url", "")), name=str(s.get("name", "")))
            for s in body
            if isinstance(s, dict) and "id" in s
        ]

    def projects(self, access_token: str, cloud_id: str) -> list[JiraProject]:
        """Projects the connecting person can see on the site, for picking one."""
        body = self._get(f"{API_ROOT}/{cloud_id}/rest/api/3/project/search", access_token)
        values = body.get("values", []) if isinstance(body, dict) else []
        return [JiraProject(key=str(p["key"]), name=str(p.get("name", ""))) for p in values]

    def _get(self, url: str, access_token: str) -> Any:
        try:
            response = self._http.get(
                url,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise AutuneError("could not reach Atlassian") from exc
        if response.status_code >= 400:
            raise AutuneError(f"Atlassian answered {response.status_code}")
        return response.json()


@lru_cache(maxsize=1)
def _default_http() -> httpx.Client:
    return httpx.Client(timeout=10.0)


def get_atlassian_client() -> AtlassianOAuthClient:
    settings = get_settings()
    return AtlassianOAuthClient(
        client_id=settings.jira_client_id,
        client_secret=settings.jira_client_secret,
        redirect_uri=settings.jira_redirect_uri,
        scopes=settings.jira_scopes,
        http=_default_http(),
    )
