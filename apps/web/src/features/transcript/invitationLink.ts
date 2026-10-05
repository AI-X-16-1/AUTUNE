/**
 * The invitation link, and the token's short stay in the browser (#552).
 *
 * **The token rides in the fragment**, `…/invite#<token>`. A fragment is never
 * sent to a server, so the token reaches no access log and no `Referer` on its
 * way in; the page reads it and posts it in a request body.
 *
 * **It is kept in `sessionStorage` across sign-in.** Somebody who opens the
 * link signed out goes to Google and comes back to `/invite` without the
 * fragment. `sessionStorage` is this tab's and this origin's, survives that
 * round trip, and ends with the tab. It is cleared as soon as the invitation
 * is accepted or refused.
 */

export const INVITE_PATH = "/invite";

const KEY = "autune.invitation";

export function invitationLink(origin: string, token: string): string {
  return `${origin.replace(/\/+$/, "")}${INVITE_PATH}#${token}`;
}

/**
 * The token this visit is about: from the address bar if the link was just
 * opened -- taken out of it at once, so it is not left in the history -- else
 * the one kept from before sign-in.
 */
export function takeToken(): string | null {
  const fromLink = window.location.hash.replace(/^#/, "").trim();
  if (fromLink) {
    keep(fromLink);
    window.history.replaceState(null, "", window.location.pathname);
    return fromLink;
  }
  try {
    return window.sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function keep(token: string): void {
  try {
    window.sessionStorage.setItem(KEY, token);
  } catch {
    // Storage refused (a locked-down browser): the token still works for
    // someone already signed in, which needs no round trip.
  }
}

export function forgetToken(): void {
  try {
    window.sessionStorage.removeItem(KEY);
  } catch {
    // Nothing was kept.
  }
}
