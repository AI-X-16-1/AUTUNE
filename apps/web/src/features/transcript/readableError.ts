import { ApiError } from "@/shared/api/client";

/** Any Hangul syllable or jamo: the sign a message was written for the reader. */
const HANGUL = /[ᄀ-ᇿ㄰-㆏가-힣]/;

/**
 * What to show a person for a failure, in Korean (#1176).
 *
 * The server's messages are written for developers: English, and sometimes
 * naming an internal id. A 500 behind the proxy is just its status text,
 * "Internal Server Error". None of that belongs on a screen. So a caught
 * error's own message is shown only when it was written in Korean -- this
 * feature's own sentences, thrown to be shown -- and never for a server
 * failure (5xx), whatever it says. Everything else reads `fallback`, the
 * caller's sentence for what did not happen.
 */
export function readableError(caught: unknown, fallback: string): string {
  if (!(caught instanceof Error)) return fallback;
  if (caught instanceof ApiError && caught.status >= 500) return fallback;
  return HANGUL.test(caught.message) ? caught.message : fallback;
}
