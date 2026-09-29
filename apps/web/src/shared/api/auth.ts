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
