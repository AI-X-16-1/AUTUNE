/**
 * Sign-in calls. Auth is not a module, so it does not go through the per-module
 * `api` object in ./client — it has its own surface here, mirroring the
 * hand-mounted `/api/auth` router on the backend.
 *
 * The session is an HttpOnly cookie set by the OAuth callback, so every call
 * here sends credentials and never touches a token in JS.
 */
import { API_BASE, ApiError } from "./client";

export { ApiError } from "./client";

/** Providers whose backend (`/api/auth/<p>/start`) is not wired yet. */
export const PENDING_PROVIDERS = new Set(["slack"]);

export interface SessionUser {
  id: string;
  email: string;
  display_name: string;
  /** The teams this person belongs to: what S28 settings (#496) picks from. */
  teams: { id: string; name: string }[];
}

/**
 * Which team a team-integration call is about: a meeting the screen shows (the
 * 액션 tab) or the team itself (S28 settings, #496). The server checks the
 * person belongs to that team either way.
 */
export type IntegrationScope = { meetingId: string } | { teamId: string };

export function scopeQuery(scope: IntegrationScope): string {
  return "meetingId" in scope
    ? `meeting_id=${encodeURIComponent(scope.meetingId)}`
    : `team_id=${encodeURIComponent(scope.teamId)}`;
}

function authUrl(path: string): string {
  return `${API_BASE}/api/auth${path}`;
}

/** Where the browser goes to begin Google sign-in. A full navigation, not fetch. */
export function googleStartUrl(redirectTo = "/"): string {
  return authUrl(`/google/start?redirect_to=${encodeURIComponent(redirectTo)}`);
}

/** Which providers this server can complete sign-in with. */
export interface Providers {
  google: boolean;
}

/**
 * Null when the server could not be asked. The caller then leaves the buttons
 * enabled: a failed probe is not evidence that sign-in is off.
 */
export async function getProviders(): Promise<Providers | null> {
  try {
    const response = await fetch(authUrl("/providers"), {
      credentials: "include",
    });
    if (!response.ok) return null;
    return (await response.json()) as Providers;
  } catch {
    return null;
  }
}

/** The current user, or null when there is no valid session. */
export async function getSession(): Promise<SessionUser | null> {
  try {
    const response = await fetch(authUrl("/me"), { credentials: "include" });
    if (response.status === 401 || response.status === 403) return null;
    if (!response.ok) return null;
    return (await response.json()) as SessionUser;
  } catch {
    return null;
  }
}

/** One document and version a person agreed to, as the server recorded it. */
export interface RecordedConsent {
  document: string;
  version: string;
  agreed_at?: string | null;
}

/**
 * What the signed-in person has agreed to, or null when it could not be read
 * -- no session, a network failure, or a server from before the record
 * existed. Null is "unknown", not "nothing": the caller decides.
 */
export async function getConsents(): Promise<RecordedConsent[] | null> {
  try {
    const response = await fetch(authUrl("/consents"), {
      credentials: "include",
    });
    if (!response.ok) return null;
    const body = (await response.json()) as { consents: RecordedConsent[] };
    return body.consents;
  } catch {
    return null;
  }
}

/** Record that the signed-in person agrees to each document and version. */
export async function agreeToConsents(
  consents: readonly { document: string; version: string }[],
): Promise<RecordedConsent[]> {
  const response = await fetch(authUrl("/consents"), {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ consents }),
  });
  if (!response.ok) {
    throw new ApiError(
      response.status,
      "consent_failed",
      "consent was not recorded",
    );
  }
  const body = (await response.json()) as { consents: RecordedConsent[] };
  return body.consents;
}

export async function logout(): Promise<void> {
  await fetch(authUrl("/logout"), { method: "POST", credentials: "include" });
}

/**
 * Ask for a magic-link email. The backend endpoint lands with the magic-link
 * work; until then a 404 surfaces as a "not ready" message rather than a raw
 * error.
 */
export async function requestMagicLink(email: string): Promise<void> {
  const response = await fetch(authUrl("/magic-link"), {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email }),
  });

  if (response.ok) return;

  if (response.status === 404) {
    throw new ApiError(404, "not_ready", "이메일 링크 로그인은 곧 제공됩니다.");
  }

  let message = "이메일을 보내지 못했습니다. 잠시 후 다시 시도해 주세요.";
  try {
    const body = (await response.json()) as { error?: { message?: string } };
    if (body?.error?.message) message = body.error.message;
  } catch {
    // keep the default
  }
  throw new ApiError(response.status, "magic_link_failed", message);
}

/**
 * Where the browser goes to connect the signed-in person's own Google Calendar
 * (#435). A full navigation: Google's consent screen, then back to `redirectTo`
 * with `?calendar=connected`.
 */
export function googleCalendarConnectUrl(redirectTo = "/"): string {
  return authUrl(
    `/google/calendar/start?redirect_to=${encodeURIComponent(redirectTo)}`,
  );
}

/** Whether the signed-in person has connected their own calendar. */
export async function getCalendarConnection(): Promise<{
  connected: boolean;
} | null> {
  try {
    const response = await fetch(authUrl("/google/calendar"), {
      credentials: "include",
    });
    if (!response.ok) return null;
    return (await response.json()) as { connected: boolean };
  } catch {
    return null;
  }
}

/**
 * Revoke the grant at Google and forget it. `revoked` is false when Google did
 * not confirm -- the connection is gone here either way.
 */
export async function disconnectCalendar(): Promise<{ revoked: boolean }> {
  const response = await fetch(authUrl("/google/calendar/disconnect"), {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok) {
    throw new ApiError(
      response.status,
      "calendar_disconnect_failed",
      "disconnect failed",
    );
  }
  return (await response.json()) as { revoked: boolean };
}

/** A team's Jira connection, as a member of the meeting's team sees it. */
export interface JiraConnection {
  connected: boolean;
  needs_reconnect?: boolean;
  site_name?: string | null;
  /** The team's Jira site, `https://` or absent. */
  site_url?: string | null;
  project_key?: string | null;
  /** The key of a chosen project that has since been deleted in Jira. */
  project_missing?: string | null;
  projects?: { key: string; name: string }[];
}

/**
 * Where the browser goes to connect the team's Jira (#82, #428): Atlassian's
 * consent screen, then back to `redirectTo` with `?jira=connected|failed`. The
 * team is the meeting's, checked against the person's membership.
 */
export function jiraConnectUrl(scope: IntegrationScope, redirectTo = "/"): string {
  return authUrl(
    `/jira/start?${scopeQuery(scope)}&redirect_to=${encodeURIComponent(redirectTo)}`,
  );
}

export async function getJiraConnection(
  scope: IntegrationScope,
): Promise<JiraConnection | null> {
  try {
    const response = await fetch(
      authUrl(`/jira?${scopeQuery(scope)}`),
      {
        credentials: "include",
      },
    );
    if (!response.ok) return null;
    return (await response.json()) as JiraConnection;
  } catch {
    return null;
  }
}

export async function chooseJiraProject(
  scope: IntegrationScope,
  projectKey: string,
): Promise<void> {
  const query = `${scopeQuery(scope)}&project_key=${encodeURIComponent(projectKey)}`;
  const response = await fetch(authUrl(`/jira/project?${query}`), {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok) {
    throw new ApiError(
      response.status,
      "jira_project_failed",
      "choosing a project failed",
    );
  }
}

export async function disconnectJira(scope: IntegrationScope): Promise<void> {
  const response = await fetch(
    authUrl(`/jira/disconnect?${scopeQuery(scope)}`),
    { method: "POST", credentials: "include" },
  );
  if (!response.ok) {
    throw new ApiError(
      response.status,
      "jira_disconnect_failed",
      "disconnect failed",
    );
  }
}

/** A team's Notion connection, as a member of the meeting's team sees it. */
export interface NotionConnection {
  connected: boolean;
  workspace_name?: string | null;
}

/**
 * Where the browser goes to connect the team's Notion workspace (#428): Notion's
 * consent screen (where the person also picks the pages Autune may see), then
 * back to `redirectTo` with `?notion=connected|failed`.
 */
export function notionConnectUrl(scope: IntegrationScope, redirectTo = "/"): string {
  return authUrl(
    `/notion/start?${scopeQuery(scope)}&redirect_to=${encodeURIComponent(redirectTo)}`,
  );
}

export async function getNotionConnection(
  scope: IntegrationScope,
): Promise<NotionConnection | null> {
  try {
    const response = await fetch(
      authUrl(`/notion?${scopeQuery(scope)}`),
      {
        credentials: "include",
      },
    );
    if (!response.ok) return null;
    return (await response.json()) as NotionConnection;
  } catch {
    return null;
  }
}

export async function disconnectNotion(scope: IntegrationScope): Promise<void> {
  const response = await fetch(
    authUrl(`/notion/disconnect?${scopeQuery(scope)}`),
    { method: "POST", credentials: "include" },
  );
  if (!response.ok) {
    throw new ApiError(
      response.status,
      "notion_disconnect_failed",
      "disconnect failed",
    );
  }
}

/** A team's Slack install, as a member of the meeting's team sees it. */
export interface SlackConnection {
  connected: boolean;
  workspace_name?: string | null;
  channel_name?: string | null;
  /** The alert channel in the team's workspace; absent on an old install. */
  channel_url?: string | null;
}

/**
 * Where the browser goes to install Autune's bot in the team's Slack (#428):
 * Slack's install screen, then back to `redirectTo` with `?slack=connected|failed`.
 * The install makes `#autune` (or joins it) for the team's alerts.
 */
export function slackConnectUrl(scope: IntegrationScope, redirectTo = "/"): string {
  return authUrl(
    `/slack/start?${scopeQuery(scope)}&redirect_to=${encodeURIComponent(redirectTo)}`,
  );
}

export async function getSlackConnection(
  scope: IntegrationScope,
): Promise<SlackConnection | null> {
  try {
    const response = await fetch(
      authUrl(`/slack?${scopeQuery(scope)}`),
      {
        credentials: "include",
      },
    );
    if (!response.ok) return null;
    return (await response.json()) as SlackConnection;
  } catch {
    return null;
  }
}

export async function disconnectSlack(
  scope: IntegrationScope,
): Promise<{ revoked: boolean; shared?: boolean }> {
  const response = await fetch(
    authUrl(`/slack/disconnect?${scopeQuery(scope)}`),
    { method: "POST", credentials: "include" },
  );
  if (!response.ok) {
    throw new ApiError(
      response.status,
      "slack_disconnect_failed",
      "disconnect failed",
    );
  }
  return (await response.json()) as { revoked: boolean; shared?: boolean };
}

/**
 * Where the browser goes to link the signed-in person's own Slack account, so
 * direct messages can reach them (#255): "Sign in with Slack", member id only.
 */
export function slackMeConnectUrl(redirectTo = "/"): string {
  return authUrl(
    `/slack/me/start?redirect_to=${encodeURIComponent(redirectTo)}`,
  );
}

export async function getSlackMe(): Promise<{
  linked: boolean;
  /** A link waiting for the Slack account to confirm it (#478). */
  pending?: boolean;
  workspace_name?: string | null;
} | null> {
  try {
    const response = await fetch(authUrl("/slack/me"), {
      credentials: "include",
    });
    if (!response.ok) return null;
    return (await response.json()) as {
      linked: boolean;
      pending?: boolean;
      workspace_name?: string | null;
    };
  } catch {
    return null;
  }
}

export async function unlinkSlackMe(): Promise<void> {
  const response = await fetch(authUrl("/slack/me/disconnect"), {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok) {
    throw new ApiError(
      response.status,
      "slack_me_unlink_failed",
      "unlink failed",
    );
  }
}
