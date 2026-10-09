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
 * against this origin and the result must still be this origin.
 *
 * **Then the answer itself is read the same way.** What is followed is a
 * string -- the resolved path, query and fragment -- and the browser resolves
 * it once more. A resolved path can begin with two slashes: `/.//example.com`
 * and `/%2e//example.com` are this origin as written and `//example.com` as
 * answered, which is another site (reviews of #1031). So the answer is
 * resolved against this origin too, and is followed only when it is this
 * origin and reads back as exactly itself. That one check also refuses an
 * answer no address can be made of (`//`), and one that names this site as a
 * host -- `//<this host>/consent` would have come back to this page past the
 * line below.
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
  const answer = url.pathname + url.search + url.hash;
  let read: URL;
  try {
    read = new URL(answer, origin);
  } catch {
    return home;
  }
  // Said for itself, though the line after it implies it: an answer that reads
  // back as exactly itself cannot have named a host.
  if (read.origin !== origin) return home;
  if (read.pathname + read.search + read.hash !== answer) return home;
  return answer as Route;
}
