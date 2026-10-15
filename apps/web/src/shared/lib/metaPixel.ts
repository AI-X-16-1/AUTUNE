/**
 * The Meta Pixel: what it may see, and the switch that keeps it away.
 *
 * Built in only where `NEXT_PUBLIC_META_PIXEL_ID` is set at build time -- the
 * release host, from its secret store. Every other build (the dev site, a
 * laptop, CI) has no id and loads nothing, so nobody's visit there reaches
 * the advertising data.
 *
 * **Screens before sign-in only** (the privacy owner, 2026-10-15). The pixel
 * sends the page's address with every event, and inside the app an address
 * names a team or a meeting. So it runs on `PIXEL_PATHS` and nowhere else:
 * a landing visit and a sign-up are what an ad is measured by. `/invite` is
 * left out on purpose -- its address carries the invitation token after `#`,
 * and the pixel sends the whole address.
 *
 * **Nothing on the page is read.** `autoConfig` off stops the pixel from
 * reading buttons and page metadata on its own, and `disablePushState` stops
 * it from counting in-app navigation as page views by itself; every event it
 * sends is one this module asks for. Automatic advanced matching (reading an
 * e-mail field) is a setting in Meta's Events Manager, not here, and must
 * stay off there: the sign-in screen has an e-mail field.
 *
 * A person may refuse in the browser they use (privacy policy 제12조): the
 * choice is kept in `localStorage`, and with it set the script is never
 * loaded. That is per browser, which is what the policy says.
 */

export const PIXEL_ID = process.env.NEXT_PUBLIC_META_PIXEL_ID ?? "";

/** The screens the pixel may run on: before a person is anybody in the app. */
export const PIXEL_PATHS: readonly string[] = ["/login", "/consent", "/legal"];

const OPT_OUT_KEY = "autune.metaPixel.optOut";

type Fbq = ((...args: unknown[]) => void) & {
  callMethod?: (...args: unknown[]) => void;
  queue?: unknown[];
  loaded?: boolean;
  version?: string;
  push?: Fbq;
  disablePushState?: boolean;
};

declare global {
  interface Window {
    fbq?: Fbq;
    _fbq?: Fbq;
  }
}

export function isPixelPath(pathname: string | null): boolean {
  if (pathname === null) return false;
  return PIXEL_PATHS.some(
    (path) => pathname === path || pathname.startsWith(`${path}/`),
  );
}

export function pixelOptedOut(): boolean {
  try {
    return window.localStorage.getItem(OPT_OUT_KEY) === "1";
  } catch {
    // Storage refused: the choice cannot be read, so it cannot be honoured
    // either -- load nothing rather than guess the person agreed.
    return true;
  }
}

/** Whether this build, this browser and this screen may run the pixel. */
export function pixelAllowed(pathname: string | null): boolean {
  return PIXEL_ID !== "" && isPixelPath(pathname) && !pixelOptedOut();
}

/**
 * Refuse in this browser: never load the script again, stop the one already
 * on the page, and drop the browser id the pixel set on this site (`_fbp`).
 */
export function optOutOfPixel(): void {
  try {
    window.localStorage.setItem(OPT_OUT_KEY, "1");
  } catch {
    // Nothing kept; `pixelOptedOut` already treats a refused store as refused.
  }
  window.fbq?.("consent", "revoke");
  // `_fbp` is set on the widest domain the pixel may write, which is not
  // knowable from here, so every suffix of the host is tried.
  const parts = window.location.hostname.split(".");
  for (let i = 0; i < parts.length - 1; i += 1) {
    const domain = parts.slice(i).join(".");
    document.cookie = `_fbp=; Max-Age=0; path=/; domain=${domain}`;
  }
  document.cookie = "_fbp=; Max-Age=0; path=/";
}

/** Meta's base code, minus the parts that read the page on their own. */
export function loadPixel(): void {
  if (window.fbq) return;
  const fbq: Fbq = function (...args: unknown[]) {
    if (fbq.callMethod) fbq.callMethod(...args);
    else fbq.queue?.push(args);
  };
  fbq.push = fbq;
  fbq.loaded = true;
  fbq.version = "2.0";
  fbq.queue = [];
  fbq.disablePushState = true;
  window.fbq = fbq;
  window._fbq = fbq;

  const script = document.createElement("script");
  script.async = true;
  script.src = "https://connect.facebook.net/en_US/fbevents.js";
  document.head.appendChild(script);

  fbq("set", "autoConfig", false, PIXEL_ID);
  fbq("init", PIXEL_ID);
}

export function trackPageView(): void {
  if (pixelOptedOut()) return;
  window.fbq?.("track", "PageView");
}

/**
 * A first agreement on `/consent` is a sign-up: nobody reaches the app
 * without one, and a person asked again after a document changed had agreed
 * before. Sent only when the pixel is already on the page, which means the
 * build, the screen and the browser all allow it.
 */
export function trackRegistration(): void {
  if (pixelOptedOut()) return;
  window.fbq?.("track", "CompleteRegistration");
}
