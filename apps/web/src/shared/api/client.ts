/**
 * The one place that talks to the API.
 *
 * Payload types come from @autune/contracts, which is generated from the
 * Pydantic models — never hand-write a type that mirrors a contract.
 * See docs/architecture/contracts.md.
 */
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** The API origin. Needed for full-page navigations the browser must follow
 *  itself, such as the OAuth authorize redirect (`/api/auth/google/start`). */
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

/** The error body every endpoint returns (autune_core.errors). */
interface ErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> };
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init.headers },
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
