import type { Route } from "next";

/**
 * Where the consent page sends a person once nothing is left to agree to:
 * the `?next=` the session gate wrote, when it is a path on this site.
 *
 * **Read the way the browser will read it.** `next` comes from the address
 * bar, so anyone can write it into a link. Looking at its first characters is
 * not enough: a browser reads a backslash as a slash and drops tabs and
 * newlines, so a slash followed by a backslash, or by a tab and a slash,
 * begins with one slash and still opens another site. The value is resolved
 * against this origin, the result must still be this origin, and what is
 * followed is the resolved path -- the thing that was checked, not the text it
 * was written as.
 *
 * Never this page again, and the home screen for anything refused.
 */
export function nextPath(next: string | null, origin: string): Route {
  const home = "/" as Route;
  if (next === null || !next.startsWith("/")) return home;
  let url: URL;
  try {
    url = new URL(next, origin);
  } catch {
    return home;
  }
  if (url.origin !== origin) return home;
  if (url.pathname === "/consent" || url.pathname.startsWith("/consent/")) return home;
  return (url.pathname + url.search + url.hash) as Route;
}
