/**
 * The app's Content-Security-Policy (#1373), sent on every response by
 * `next.config.ts`.
 *
 * **What it is for.** A script that runs on the app's origin can call `/api`
 * with the session cookie. The policy names every origin a script may come
 * from, so a script from anywhere else does not run here -- the same reason
 * the ad pixel stays off the app's origin (#1320, #1355). A third-party script
 * the app takes on later (Google's Picker, #1369) is added here by name, in
 * the pull request that loads it.
 *
 * **What it lists is what the app does today**, and nothing for later:
 *
 * - scripts, the `pcm-worklet.js` audio worklet included, and styles from the
 *   app's own origin. Next.js writes inline `<script>` elements into every
 *   page and the screens set `style` attributes, so both allow
 *   `'unsafe-inline'`: the policy binds where a script is *loaded from*, not
 *   whether an inline one runs. A nonce would cover that too, at the cost of
 *   rendering every page per request.
 * - fetches and the live transcript socket on the app's own origin, or on
 *   `NEXT_PUBLIC_LIVE_URL` when a build sets it (`features/transcript/api.ts`).
 * - Google's file preview in a frame (`shared/drive/driveLink.ts`,
 *   `drivePreviewUrl`).
 * - images, fonts and everything else not named: the app's own origin only
 *   (`default-src`).
 * - no plug-ins, no `<base>` pointing elsewhere, forms only to the app itself,
 *   and no page of the app inside another site's frame.
 *
 * `next dev` evaluates code for hot reload, so development adds
 * `'unsafe-eval'` and nothing else.
 */

/** Origins Google's file preview loads from (`drivePreviewUrl`). */
export const DRIVE_PREVIEW_ORIGINS = ["https://drive.google.com", "https://docs.google.com"];

export type PolicyInputs = {
  /** `next dev` rather than a build. */
  development: boolean;
  /** `NEXT_PUBLIC_LIVE_URL`: where the live socket opens when it is not the page's origin. */
  liveUrl?: string;
};

/** The origin of `NEXT_PUBLIC_LIVE_URL`, or none when it is blank or not a ws/wss URL. */
function liveOrigin(liveUrl: string | undefined): string | null {
  const value = (liveUrl ?? "").trim();
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "ws:" || url.protocol === "wss:" ? url.origin : null;
  } catch {
    return null;
  }
}

/** The policy as one header value. */
export function contentSecurityPolicy({ development, liveUrl }: PolicyInputs): string {
  const live = liveOrigin(liveUrl);
  const directives: [string, ...string[]][] = [
    ["default-src", "'self'"],
    ["script-src", "'self'", "'unsafe-inline'", ...(development ? ["'unsafe-eval'"] : [])],
    ["style-src", "'self'", "'unsafe-inline'"],
    ["connect-src", "'self'", ...(live ? [live] : [])],
    ["frame-src", ...DRIVE_PREVIEW_ORIGINS],
    ["object-src", "'none'"],
    ["base-uri", "'self'"],
    ["form-action", "'self'"],
    ["frame-ancestors", "'none'"],
  ];
  return directives.map((directive) => directive.join(" ")).join("; ");
}
