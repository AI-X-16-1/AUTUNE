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
import re
import secrets
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from autune_core.errors import AutuneError, PermissionDeniedError
from autune_core.settings import get_settings

AUTHORIZE_ENDPOINT = "https://slack.com/oauth/v2/authorize"

_DM_CHANNEL = re.compile(r"D[A-Z0-9]+")
_WORKSPACE = re.compile(r"T[A-Z0-9]+")


def dm_open_url(workspace_id: str, channel_id: str | None) -> str | None:
    """Slack's own redirect that opens a conversation in the app, or on the web
    when the app is not installed -- for the screen's "open the DM" link. Ids
    only, checked for shape, since the result goes into an ``href``."""
    if not channel_id or not _DM_CHANNEL.fullmatch(channel_id):
        return None
    if not _WORKSPACE.fullmatch(workspace_id):
        return None
    return f"https://slack.com/app_redirect?team={workspace_id}&channel={channel_id}"


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


class SlackIdentityRefusedError(AutuneError):
    """A Slack account that cannot be linked for this person; the subclass's
    code tells the screen which case it is."""

    status_code = 409


class SlackTeamNotConnectedError(SlackIdentityRefusedError):
    """None of the person's teams has installed Autune in Slack yet, so no bot
    could send them anything."""

    code = "slack_team_not_connected"


class SlackWrongWorkspaceError(SlackIdentityRefusedError):
    """The browser signed in to a workspace none of the person's teams
    installed Autune in -- a personal workspace, say. Linking it would say
    "linked" while every DM went nowhere."""

    code = "slack_wrong_workspace"


class SlackAccountTakenError(SlackIdentityRefusedError):
    """That Slack account is already linked to another Autune person -- most
    likely a Slack session left in a shared browser. Linking it again would
    send this person's DMs, speaking ratio included, to someone else."""

    code = "slack_account_taken"


class SlackLinkNotConfirmedError(SlackIdentityRefusedError):
    """The confirmation link was not this person's, was used up, or expired.
    A link is confirmed only by the Autune session that started it (#478)."""

    code = "slack_link_not_confirmed"


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
class SlackIdentity:
    """Who a person is in Slack -- their member id and workspace, nothing more."""

    user_id: str
    team_id: str


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

    def identity_url(self, *, state: str, nonce: str) -> str:
        """ "Sign in with Slack" asking for ``openid`` only: the member id and
        workspace, no email, no profile (#70)."""
        query = urlencode(
            {
                "response_type": "code",
                "scope": "openid",
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "state": state,
                "nonce": nonce,
            }
        )
        return f"https://slack.com/openid/connect/authorize?{query}"

    def identify(self, code: str, *, nonce: str) -> SlackIdentity:
        """Trade a Sign-in-with-Slack code for the person's member id and
        workspace.

        The ID token's ``nonce`` must be the one this flow sent. Its signature
        is not checked: it came straight from Slack's token endpoint over TLS,
        in answer to our client secret, which OpenID Connect Core 3.1.3.7
        accepts in place of a signature check. The user token is used once for
        ``userInfo`` and then revoked -- nothing personal is kept but the ids."""
        answer = self._call(
            "openid.connect.token",
            data={
                "code": code,
                "redirect_uri": self._redirect_uri,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "authorization_code",
            },
            refused="Slack rejected the sign-in code",
        )
        token = answer.get("access_token")
        if not token:
            raise PermissionDeniedError("Slack returned no sign-in token")
        try:
            try:
                claims = jwt.decode(
                    str(answer.get("id_token") or ""), options={"verify_signature": False}
                )
            except jwt.InvalidTokenError as exc:
                raise PermissionDeniedError("Slack returned no usable ID token") from exc
            if not nonce or claims.get("nonce") != nonce:
                raise PermissionDeniedError("Slack ID token nonce does not match the request")
            info = self._call(
                "openid.connect.userInfo",
                data={},
                token=str(token),
                refused="Slack refused userInfo",
            )
        finally:
            self.revoke(str(token))
        user_id = info.get("https://slack.com/user_id") or info.get("sub")
        team_id = info.get("https://slack.com/team_id", "")
        if not user_id:
            raise PermissionDeniedError("Slack returned no user id")
        return SlackIdentity(user_id=str(user_id), team_id=str(team_id))

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
            self._discard(token, channel)
            raise

    def _discard(self, token: str, channel: str) -> None:
        """Put away a private channel nobody could be invited to. An archived
        channel keeps its name, so it is renamed first: otherwise every failed
        install would use up ``#autune``, then ``#autune-2``, and the tenth
        would blame the workspace for names Autune itself is holding."""
        with contextlib.suppress(AutuneError):
            self._call(
                "conversations.rename",
                data={"channel": channel, "name": f"autune-unused-{secrets.token_hex(4)}"},
                token=token,
                refused="rename refused",
            )
        with contextlib.suppress(AutuneError):
            self._call(
                "conversations.archive",
                data={"channel": channel},
                token=token,
                refused="archive refused",
                allow={"already_archived"},
            )

    def channel_usable(self, token: str, channel: str) -> bool:
        """Whether a kept channel can still take posts: it exists, is not
        archived, and the bot is in it (``conversations.info``, ``groups:read``)."""
        try:
            body = self._call(
                "conversations.info",
                data={"channel": channel},
                token=token,
                refused="Slack refused to describe the channel",
            )
        except AutuneError:
            return False
        info = body.get("channel") or {}
        return bool(info) and not info.get("is_archived") and info.get("is_member", True)

    def send_link_confirmation(self, token: str, member_id: str, text: str) -> str | None:
        """One direct message from the team's bot to ``member_id`` --
        ``chat.postMessage`` to a member id opens the DM (``chat:write``).
        Returns the DM's channel id (``D...``) so the screen can open it, or
        ``None`` if Slack's answer did not carry one that looks like it.

        The only message core sends: fixed words and a link, never meeting
        content. That is why it does not go through ``autune_integrations``'
        outbound guard, which core cannot import (the guard imports core)."""
        body = self._call(
            "chat.postMessage",
            data={"channel": member_id, "text": text},
            token=token,
            refused="Slack refused the confirmation message",
        )
        channel = str(body.get("channel") or "")
        return channel if _DM_CHANNEL.fullmatch(channel) else None

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
