"""Slack over OAuth v2 ("Add to Slack") -- a team installs Autune's bot with one
click and gets an alert channel made for it (#428).

1. ``authorization_url`` sends the browser to Slack's install screen for the bot
   scopes below.
2. ``exchange_code`` trades the code for the workspace's bot token
   (``oauth.v2.access``).
3. ``create_alert_channel`` makes a **private** ``#autune`` and invites the
   person who installed, so the modules that post (D's briefing, E's report)
   have a channel without anyone typing an id. It never joins a channel that
   already exists: two Autune teams in one workspace, or a company channel that
   happens to be called ``#autune``, would otherwise read one team's decisions
   (review of #468). A taken name becomes ``#autune-2``, ``#autune-3``...

**The token is the workspace bot's**, as Notion's is: bot scopes survive the
installer leaving (``external-approvals.md``). Only bot scopes are asked for.

**One scope beyond the manifest's, with the reason written down**
(``external-approvals.md`` asks for that): ``groups:write``, to create the
private channel and invite the installer. No user scope and no
``users:read.email`` -- the installer's member id comes with the install, and
the directory stays unread (#70).

Slack requires an **HTTPS** redirect URL, so this flow cannot finish on plain
``http://localhost``; see the environment docs.
"""

from __future__ import annotations

import contextlib
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
    "groups:write",
)

NAME_ATTEMPTS = 10


class SlackChannelUnavailableError(AutuneError):
    """Every name from ``#autune`` to ``#autune-10`` is taken. A person has to
    free one, or set another ``AUTUNE_SLACK_CHANNEL_NAME``."""

    code = "slack_channel_unavailable"
    status_code = 409


@dataclass(frozen=True)
class SlackInstall:
    access_token: str
    bot_user_id: str
    workspace_id: str
    workspace_name: str
    installer_id: str
    """The Slack member who clicked Allow -- invited to the private channel."""


@dataclass(frozen=True)
class SlackChannel:
    id: str
    name: str


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
            installer_id=str((body.get("authed_user") or {}).get("id", "")),
        )

    def create_alert_channel(self, token: str, name: str, *, invite: str) -> SlackChannel:
        """A new **private** channel for the team's alerts, with ``invite`` (the
        installer) in it; they add the rest of the team. ``#name`` first, then
        ``#name-2`` and on while a name is taken -- whoever holds it, the bot
        does not join it."""
        if not invite:
            raise AutuneError("Slack did not say who installed, so nobody could be invited")
        for attempt in range(1, NAME_ATTEMPTS + 1):
            candidate = name if attempt == 1 else f"{name}-{attempt}"
            created = self._call(
                "conversations.create",
                data={"name": candidate, "is_private": "true"},
                token=token,
                refused="Slack refused to create the channel",
                allow={"name_taken"},
            )
            if not created.get("ok"):
                continue
            channel = SlackChannel(
                str((created.get("channel") or {}).get("id") or ""),
                str((created.get("channel") or {}).get("name") or candidate),
            )
            if not channel.id:
                raise AutuneError("Slack made a channel but did not say which")
            self._invite(token, channel.id, invite)
            return channel
        raise SlackChannelUnavailableError(
            f"#{name} to #{name}-{NAME_ATTEMPTS} are all taken in this workspace"
        )

    def _invite(self, token: str, channel: str, member: str) -> None:
        try:
            self._call(
                "conversations.invite",
                data={"channel": channel, "users": member},
                token=token,
                refused="Slack refused to invite the installer",
                allow={"already_in_channel"},
            )
        except AutuneError:
            # A private channel only the bot can see is no use to anyone.
            with contextlib.suppress(AutuneError):
                self._call(
                    "conversations.archive",
                    data={"channel": channel},
                    token=token,
                    refused="archive refused",
                    allow={"already_archived"},
                )
            raise

    def revoke(self, token: str) -> bool:
        """End the install's token at Slack (``auth.revoke``)."""
        try:
            body = self._call("auth.revoke", data={}, token=token, refused="revoke refused")
        except AutuneError:
            return False
        return bool(body.get("revoked"))

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
        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise AutuneError(f"Slack's {method} answer was not JSON") from exc
        if not isinstance(body, dict):
            raise AutuneError(f"Slack's {method} answer was not an object")
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
