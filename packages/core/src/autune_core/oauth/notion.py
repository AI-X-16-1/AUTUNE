"""Notion over its public-integration OAuth -- a team connects its workspace with
one click (#428).

1. ``authorization_url`` sends the browser to Notion's consent screen, where the
   person also picks which pages Autune may see.
2. ``exchange_code`` trades the code for the workspace's bot token.

Unlike Atlassian and Google, the token belongs to the **workspace's bot**, not
to the person who clicked: Notion says a connection "retains access" when
whoever shared a page leaves (``external-approvals.md``). What a person can
still break is where the databases live -- a private page goes with its owner,
so the screen suggests a teamspace page.

Which page the databases go under, and the databases themselves, are module
B's (``autune_extraction.notion_setup``): the property names are B's, and the
ids live in B's own table. Core stores the token and the workspace, nothing
else.
"""

from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlencode

import httpx

from autune_core.errors import AutuneError, PermissionDeniedError
from autune_core.settings import get_settings

AUTHORIZE_ENDPOINT = "https://api.notion.com/v1/oauth/authorize"
TOKEN_ENDPOINT = "https://api.notion.com/v1/oauth/token"


@dataclass(frozen=True)
class NotionGrant:
    access_token: str
    workspace_id: str
    workspace_name: str
    bot_id: str


class NotionOAuthClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http: httpx.Client | None = None,
    ) -> None:
        if not (client_id and client_secret and redirect_uri):
            raise AutuneError("Notion connect is not configured on this server")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = http or httpx.Client(timeout=10.0)

    def authorization_url(self, *, state: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "response_type": "code",
                "owner": "user",
                "redirect_uri": self._redirect_uri,
                "state": state,
            }
        )
        return f"{AUTHORIZE_ENDPOINT}?{query}"

    def exchange_code(self, code: str) -> NotionGrant:
        basic = b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        try:
            response = self._http.post(
                TOKEN_ENDPOINT,
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._redirect_uri,
                },
                headers={"Authorization": f"Basic {basic}", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise AutuneError("could not reach Notion") from exc
        if response.status_code >= 500:
            raise AutuneError(f"Notion answered {response.status_code}")
        if response.status_code >= 400:
            # The body may echo the code; it stays out of the message.
            raise PermissionDeniedError("Notion rejected the authorization code")
        data = response.json()
        token = data.get("access_token")
        if not token:
            raise PermissionDeniedError("Notion returned no access token")
        return NotionGrant(
            access_token=str(token),
            workspace_id=str(data.get("workspace_id", "")),
            workspace_name=str(data.get("workspace_name") or ""),
            bot_id=str(data.get("bot_id", "")),
        )


@lru_cache(maxsize=1)
def _default_http() -> httpx.Client:
    return httpx.Client(timeout=10.0)


def get_notion_oauth_client() -> NotionOAuthClient:
    settings = get_settings()
    return NotionOAuthClient(
        client_id=settings.notion_client_id,
        client_secret=settings.notion_client_secret,
        redirect_uri=settings.notion_redirect_uri,
        http=_default_http(),
    )
