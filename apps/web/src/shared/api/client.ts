/**
 * The one place that talks to the API.
 *
 * Payload types come from @autune/contracts, which is generated from the
 * Pydantic models — never hand-write a type that mirrors a contract.
 * See docs/architecture/contracts.md.
 */
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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

/** The error body every endpoint returns (autune_core.errors). */
interface ErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> };
}

/** Where a developer's browser keeps its token. Set by hand; see environments.md. */
const TOKEN_KEY = "autune.token";

/**
 * The bearer token for this browser, until there is a sign-in.
 *
 * A route taking `CurrentUser` refuses a request without one, and screen S01
 * does not exist, so until it does every call carries whatever token the
 * developer has. Two sources, in order:
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
 * Nothing here is the sign-in design. When #189 lands, this function is where
 * a real token goes, and the 401 handling it needs goes beside it.
 */
function authHeaders(): HeadersInit {
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

  if (!response.ok) {
    let body: ErrorBody | undefined;
    try {
      body = (await response.json()) as ErrorBody;
    } catch {
      // Non-JSON error body; fall through to the status text.
    }
    throw new ApiError(
      response.status,
      body?.error.code ?? "unknown",
      body?.error.message ?? response.statusText,
      body?.error.details ?? {},
    );
  }

  return (await response.json()) as T;
}

/** Each module is served under /api/<module>; features call their own only. */
export const api = {
  audio: <T>(path: string, init?: RequestInit) => request<T>(`/api/audio${path}`, init),
  extraction: <T>(path: string, init?: RequestInit) => request<T>(`/api/extraction${path}`, init),
  gap: <T>(path: string, init?: RequestInit) => request<T>(`/api/gap${path}`, init),
  context: <T>(path: string, init?: RequestInit) => request<T>(`/api/context${path}`, init),
  intelligence: <T>(path: string, init?: RequestInit) =>
    request<T>(`/api/intelligence${path}`, init),
};
