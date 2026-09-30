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
    const response = await fetch(authUrl("/providers"), { credentials: "include" });
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
  return authUrl(`/google/calendar/start?redirect_to=${encodeURIComponent(redirectTo)}`);
}

/** Whether the signed-in person has connected their own calendar. */
export async function getCalendarConnection(): Promise<{ connected: boolean } | null> {
  try {
    const response = await fetch(authUrl("/google/calendar"), { credentials: "include" });
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
    throw new ApiError(response.status, "calendar_disconnect_failed", "disconnect failed");
  }
  return (await response.json()) as { revoked: boolean };
}


/** A team's Jira connection, as a member of the meeting's team sees it. */
export interface JiraConnection {
  connected: boolean;
  needs_reconnect?: boolean;
  site_name?: string | null;
  project_key?: string | null;
  projects?: { key: string; name: string }[];
}

/**
 * Where the browser goes to connect the team's Jira (#82, #428): Atlassian's
 * consent screen, then back to `redirectTo` with `?jira=connected|failed`. The
 * team is the meeting's, checked against the person's membership.
 */
export function jiraConnectUrl(meetingId: string, redirectTo = "/"): string {
  return authUrl(
    `/jira/start?meeting_id=${encodeURIComponent(meetingId)}&redirect_to=${encodeURIComponent(redirectTo)}`,
  );
}

export async function getJiraConnection(meetingId: string): Promise<JiraConnection | null> {
  try {
    const response = await fetch(authUrl(`/jira?meeting_id=${encodeURIComponent(meetingId)}`), {
      credentials: "include",
    });
    if (!response.ok) return null;
    return (await response.json()) as JiraConnection;
  } catch {
    return null;
  }
}

export async function chooseJiraProject(meetingId: string, projectKey: string): Promise<void> {
  const query = `meeting_id=${encodeURIComponent(meetingId)}&project_key=${encodeURIComponent(projectKey)}`;
  const response = await fetch(authUrl(`/jira/project?${query}`), {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok) {
    throw new ApiError(response.status, "jira_project_failed", "choosing a project failed");
  }
}

export async function disconnectJira(meetingId: string): Promise<void> {
  const response = await fetch(
    authUrl(`/jira/disconnect?meeting_id=${encodeURIComponent(meetingId)}`),
    { method: "POST", credentials: "include" },
  );
  if (!response.ok) {
    throw new ApiError(response.status, "jira_disconnect_failed", "disconnect failed");
  }
}
