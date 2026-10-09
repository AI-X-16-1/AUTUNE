/**
 * The one place that talks to the API.
 *
 * Payload types come from @autune/contracts, which is generated from the
 * Pydantic models — never hand-write a type that mirrors a contract.
 * See docs/architecture/contracts.md.
 */
// In the browser, talk to our own origin and let Next rewrite `/api/*` to the
// API (see next.config.ts) — that keeps the session cookie first-party. On the
// server there is no origin, so fall back to a direct address.
const BASE =
  process.env.NEXT_PUBLIC_API_URL ??
  (typeof window === "undefined" ? "http://localhost:8000" : "");

/** The API prefix for this context. `""` in the browser (same-origin, proxied);
 *  a full origin on the server. Prepend it to a path, e.g. for the OAuth
 *  authorize navigation (`/api/auth/google/start`). */
export const API_BASE = BASE;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * The error body our own handlers return (autune_core.errors). Every field is
 * read as possibly absent: not every failure is ours. The framework answers a
 * route that is not mounted with `{"detail": "Not Found"}` and a body that
 * fails its schema with `{"detail": [...]}`, and a proxy in front of the API
 * answers with whatever it likes.
 */
interface ErrorBody {
  error?: { code?: unknown; message?: unknown; details?: unknown } | null;
}

/**
 * A failed answer as an `ApiError`, whatever its body holds.
 *
 * Only `error`'s own fields are taken, each only when it is what `ApiError`
 * says it is. A body without them is still a failure with a status: reading
 * `error.code` off it used to throw a `TypeError` in place of the `ApiError`
 * every caller checks for, so a screen showed "Cannot read properties of
 * undefined" where it had a sentence ready for a 404. Nothing else of the body
 * is kept -- the framework's 422 repeats what was sent, and what was sent can
 * be a person's own words.
 */
async function failure(response: Response): Promise<ApiError> {
  let error: NonNullable<ErrorBody["error"]> = {};
  try {
    const body = (await response.json()) as ErrorBody | null;
    error = body?.error ?? {};
  } catch {
    // Non-JSON error body; the status is what there is.
  }
  return new ApiError(
    response.status,
    typeof error.code === "string" ? error.code : "unknown",
    typeof error.message === "string" ? error.message : response.statusText,
    error.details !== null && typeof error.details === "object" && !Array.isArray(error.details)
      ? (error.details as Record<string, unknown>)
      : {},
  );
}

/** Where a developer's browser keeps its token. Set by hand; see environments.md. */
const TOKEN_KEY = "autune.token";

/**
 * Whether this tab has a signed-in session (#440).
 *
 * The session is an HttpOnly cookie, so this code cannot see it; `SessionGate`
 * asks `/api/auth/me` before any screen draws and reports what it found here.
 * The flag lives in memory only, so a reload asks again.
 */
let signedIn = false;

/**
 * Tell the client whether a session cookie was found. Called by `SessionGate`
 * once its `/api/auth/me` check returns, before any screen calls `api.*`.
 */
export function setSignedIn(value: boolean): void {
  signedIn = value;
}

/**
 * The developer bearer token for this browser, when it is not signed in.
 *
 * **A session wins.** The backend reads `Authorization` before the session
 * cookie (`autune_core.auth._session_token`), so a tab that signed in with
 * Google and still sent a dev token ran every module call as the dev-token
 * user while `/api/auth/me` named the Google one (#440). Once `SessionGate`
 * has found a session this returns nothing, and the cookie decides.
 *
 * Without a session, every call carries whatever token the developer has.
 * Two sources, in order:
 *
 * 1. `localStorage["autune.token"]` — pasted in by hand, so a person can switch
 *    users without a rebuild.
 * 2. `NEXT_PUBLIC_AUTUNE_DEV_TOKEN` — inlined at build time from `.env.local`.
 *
 * Both come from `POST /api/audio/dev/token`, which exists only when
 * `AUTUNE_ENV=local`.
 *
 * **This lives here rather than in a feature.** It was written in
 * `features/transcript/api.ts`, which was right when one screen needed it; a
 * second screen needing it makes a copy, and an authorisation detail that
 * exists in two places is one that can come to mean two things — the argument
 * the backend's own `require_team_member` docstring makes. Module C's read
 * routes are about to take `CurrentUser` (#276), and the gap report screen
 * calls them through this client, so the second copy was the next commit.
 *
 * **Exported for the one call that cannot go through `request`.**
 * `transcript.uploadRecording` sends a multipart body, and `request` sets
 * `content-type: application/json` on everything, so that call reaches `fetch`
 * directly (#301). It asks this function for the header rather than carrying
 * its own copy — the copy is what this file exists to remove. Nothing else
 * imports it: a call going through `api.*` already has the header.
 */
export function authHeaders(): HeadersInit {
  if (signedIn) return {};
  let token: string | null = null;
  try {
    if (typeof window !== "undefined") token = window.localStorage.getItem(TOKEN_KEY);
  } catch {
    // Private mode or blocked storage. Fall through to the build-time value.
  }
  token ??= process.env.NEXT_PUBLIC_AUTUNE_DEV_TOKEN ?? null;
  return token ? { authorization: `Bearer ${token}` } : {};
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    // `init.headers` last: a caller that passes its own `authorization` — a test
    // acting as somebody else — overrides the browser's token rather than
    // fighting it.
    headers: { "content-type": "application/json", ...authHeaders(), ...init.headers },
  });

  if (!response.ok) throw await failure(response);

  return (await response.json()) as T;
}

/**
 * Each module is served under /api/<module>; features call their own only. The
 * one exception: the transcript feature calls /api/agent/research, because the
 * research card on the meeting screen reads the agent layer's documents for
 * that meeting (Research subagent spec, section 4 ④).
 */
export const api = {
  audio: <T>(path: string, init?: RequestInit) => request<T>(`/api/audio${path}`, init),
  extraction: <T>(path: string, init?: RequestInit) => request<T>(`/api/extraction${path}`, init),
  gap: <T>(path: string, init?: RequestInit) => request<T>(`/api/gap${path}`, init),
  context: <T>(path: string, init?: RequestInit) => request<T>(`/api/context${path}`, init),
  agent: <T>(path: string, init?: RequestInit) => request<T>(`/api/agent${path}`, init),
  intelligence: <T>(path: string, init?: RequestInit) =>
    request<T>(`/api/intelligence${path}`, init),
};
