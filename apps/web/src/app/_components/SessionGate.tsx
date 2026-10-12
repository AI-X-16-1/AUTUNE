"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";

import { getConsents, getSession, type SessionUser } from "@/shared/api/auth";
import { authHeaders, setSignedIn } from "@/shared/api/client";

import { missingConsents } from "../legal/consents";

/**
 * Sends somebody with no session to `/login` before any screen draws.
 *
 * Without it, opening the app signed out drew the whole shell with
 * "no session: send a bearer token or sign in" where the meeting list should
 * be, and nothing pointed at the sign-in screen. Every screen under `(app)`
 * and `(focus)` needs a signed-in person, so the check sits in front of all of
 * them rather than in each.
 *
 * **A developer bearer token counts as signed in.** `/api/auth/me` reads the
 * session cookie only, so a browser running on `NEXT_PUBLIC_AUTUNE_DEV_TOKEN`
 * or a pasted `localStorage` token has no session there and every API call
 * still succeeds. Redirecting it would lock out the dev setup
 * environments.md describes; the token is checked first.
 *
 * **A session found here switches the dev token off** (`setSignedIn`, #440).
 * Without that, a signed-in tab would still send the developer token on every
 * module call and run as two people at once. Nothing draws until the check
 * returns, so no screen's first call goes out before the switch.
 *
 * **The `/dev-*` preview routes are let through unchecked.** `/dev-gap`,
 * `/dev-context` and `/dev-dashboard` exist to show a layout with no backend,
 * no worker and no recording (`dev-gap/page.tsx`), and `getSession` returns
 * null when the fetch fails — so the gate sent them to `/login` exactly when
 * they were being used as intended. Each of them already calls `notFound()`
 * in production, so skipping the check here opens nothing in a deployment.
 *
 * **A signed-in person who has not agreed to every required document is sent
 * to `/consent`** (the user, 2026-10-02), with the path they were on, and comes
 * back when they have. This is the whole of the gate: the server records what
 * was agreed to and refuses nothing, so the check is here, in front of every
 * screen, and nowhere else. Two cases pass without it. A developer token has
 * no person to ask. And a record that cannot be read -- a network failure, a
 * server from before the record existed -- is unknown, not missing: holding
 * everybody at a page that cannot save either would lock the app on the
 * server's bad day.
 *
 * Nothing is drawn while the check is in flight, so a signed-out visitor never
 * sees a screen fail before the redirect. The session it finds is shared
 * through `useSessionUser` so the sidebar does not ask again.
 */

const SessionContext = createContext<SessionUser | null>(null);

/** The signed-in person, or null on a developer token with no session. */
export function useSessionUser(): SessionUser | null {
  return useContext(SessionContext);
}

type GateState = { status: "checking" } | { status: "in"; user: SessionUser | null };

export function SessionGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const devPreview = usePathname().startsWith("/dev-");
  const [state, setState] = useState<GateState>({ status: "checking" });

  useEffect(() => {
    if (devPreview) return;
    let current = true;
    const hasDevToken = "authorization" in authHeaders();
    void getSession().then(async (user) => {
      if (!current) return;
      setSignedIn(user !== null);
      if (user) {
        const given = await getConsents();
        if (!current) return;
        if (given !== null && missingConsents(given).length > 0) {
          const here = window.location.pathname + window.location.search;
          router.replace(`/consent?next=${encodeURIComponent(here)}`);
          return;
        }
        setState({ status: "in", user });
      } else if (hasDevToken) {
        setState({ status: "in", user });
      } else {
        router.replace("/login");
      }
    });
    return () => {
      current = false;
    };
    // Checked once per mount. The shell stays mounted across navigation
    // inside it, and a session does not end because the path changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (devPreview) {
    return <SessionContext.Provider value={null}>{children}</SessionContext.Provider>;
  }
  if (state.status === "checking") return null;

  return (
    <SessionContext.Provider value={state.user}>{children}</SessionContext.Provider>
  );
}
