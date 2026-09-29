"""Slack over OAuth v2 ("Add to Slack") -- a team installs Autune's bot with one
click and gets an alert channel made for it (#428).

1. ``authorization_url`` sends the browser to Slack's install screen for the bot
   scopes below.
2. ``exchange_code`` trades the code for the workspace's bot token
   (``oauth.v2.access``).
3. ``ensure_channel`` makes ``#autune`` -- or joins it when the name is taken --
   so the modules that post (D's briefing, E's report) have a channel without
   anyone typing an id.

**The token is the workspace bot's**, as Notion's is: bot scopes survive the
installer leaving (``external-approvals.md``). Only bot scopes are asked for.

**Two scopes beyond the manifest's, with the reason written down**
(``external-approvals.md`` asks for that): ``channels:manage`` to create the
alert channel and ``channels:join`` to join an existing one of that name. No
user scope and no ``users:read.email`` -- the directory stays unread (#70).

Slack requires an **HTTPS** redirect URL, so this flow cannot finish on plain
``http://localhost``; see the environment docs.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

import httpx

from autune_core.errors import AutuneError, PermissionDeniedError
from autune_core.settings import get_settings

AUTHORIZE_ENDPOINT = "https://slack.com/oauth/v2/authorize"
API = "https://slack.com/api"

BOT_SCOPES = (
    "commands",
    "chat:write",
    "im:write",
    "channels:read",
    "groups:read",
    "channels:manage",
    "channels:join",
)


class SlackChannelUnavailableError(AutuneError):
    """Neither ``#autune`` nor ``#autune-alerts`` can be used: each name is
    taken by a channel the bot cannot see or join -- a private one, or an
    archived one. A person has to invite the bot or free a name."""

    code = "slack_channel_unavailable"
    status_code = 409


@dataclass(frozen=True)
class SlackInstall:
    access_token: str
    bot_user_id: str
    workspace_id: str
    workspace_name: str


@dataclass(frozen=True)
class SlackChannel:
    id: str
    name: str
    created: bool


class SlackOAuthClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http: httpx.Client | None = None,
    ) -> None:
        if not (client_id and client_secret and redirect_uri):
            raise AutuneError("Slack connect is not configured on this server")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = http or httpx.Client(timeout=10.0)

    def authorization_url(self, *, state: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "scope": ",".join(BOT_SCOPES),
                "redirect_uri": self._redirect_uri,
                "state": state,
            }
        )
        return f"{AUTHORIZE_ENDPOINT}?{query}"

    def exchange_code(self, code: str) -> SlackInstall:
        body = self._call(
            "oauth.v2.access",
            data={
                "code": code,
                "redirect_uri": self._redirect_uri,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            refused="Slack rejected the authorization code",
        )
        token = body.get("access_token")
        if not token or body.get("token_type") not in (None, "bot"):
            raise PermissionDeniedError("Slack returned no bot token")
        team = body.get("team") or {}
        return SlackInstall(
            access_token=str(token),
            bot_user_id=str(body.get("bot_user_id", "")),
            workspace_id=str(team.get("id", "")),
            workspace_name=str(team.get("name", "")),
        )

    def ensure_channel(self, token: str, name: str) -> SlackChannel:
        """``#name`` for the team's alerts: made, or joined when a public one of
        that name exists. When the name belongs to a channel the bot cannot
        join -- private or archived, found testing on a real workspace where
        someone had made a private ``#autune`` -- ``#name-alerts`` is tried the
        same way before giving up."""
        for candidate in (name, f"{name}-alerts"):
            channel = self._make_or_join(token, candidate)
            if channel is not None:
                return channel
        raise SlackChannelUnavailableError(
            f"#{name} and #{name}-alerts are taken by channels the bot cannot join"
        )

    def _make_or_join(self, token: str, name: str) -> SlackChannel | None:
        created = self._call(
            "conversations.create",
            data={"name": name},
            token=token,
            refused="Slack refused to create the channel",
            allow={"name_taken"},
        )
        if created.get("ok"):
            channel = created["channel"]
            return SlackChannel(str(channel["id"]), str(channel["name"]), created=True)
        existing = self._find_public_channel(token, name)
        if existing is None:
            return None  # taken by a private or archived channel
        self._call(
            "conversations.join",
            data={"channel": existing},
            token=token,
            refused="Slack refused to join the channel",
        )
        return SlackChannel(existing, name, created=False)

    def revoke(self, token: str) -> bool:
        """End the install's token at Slack (``auth.revoke``)."""
        try:
            body = self._call("auth.revoke", data={}, token=token, refused="revoke refused")
        except AutuneError:
            return False
        return bool(body.get("revoked"))

    def _find_public_channel(self, token: str, name: str) -> str | None:
        cursor = ""
        for _ in range(20):
            body = self._call(
                "conversations.list",
                data={
                    "types": "public_channel",
                    "exclude_archived": "true",
                    "limit": "200",
                    **({"cursor": cursor} if cursor else {}),
                },
                token=token,
                refused="Slack refused to list channels",
            )
            for channel in body.get("channels", []):
                if channel.get("name") == name:
                    return str(channel["id"])
            cursor = str((body.get("response_metadata") or {}).get("next_cursor") or "")
            if not cursor:
                return None
        return None

    def _call(
        self,
        method: str,
        *,
        data: dict[str, str],
        refused: str,
        token: str | None = None,
        allow: set[str] | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = self._http.post(f"{API}/{method}", data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise AutuneError("could not reach Slack") from exc
        if response.status_code >= 400:
            raise AutuneError(f"Slack answered {response.status_code}")
        body: dict[str, Any] = response.json()
        if not body.get("ok") and body.get("error") not in (allow or set()):
            # Slack's error code names the problem and carries no secret.
            raise PermissionDeniedError(f"{refused}: {body.get('error')}")
        return body


@lru_cache(maxsize=1)
def _default_http() -> httpx.Client:
    return httpx.Client(timeout=10.0)


def get_slack_oauth_client() -> SlackOAuthClient:
    settings = get_settings()
    return SlackOAuthClient(
        client_id=settings.slack_client_id,
        client_secret=settings.slack_client_secret,
        redirect_uri=settings.slack_redirect_uri,
        http=_default_http(),
    )
