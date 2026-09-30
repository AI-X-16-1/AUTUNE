"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";

import { getSession, type SessionUser } from "@/shared/api/auth";
import { authHeaders } from "@/shared/api/client";

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
 * **The `/dev-*` preview routes are let through unchecked.** `/dev-gap`,
 * `/dev-context` and `/dev-dashboard` exist to show a layout with no backend,
 * no worker and no recording (`dev-gap/page.tsx`), and `getSession` returns
 * null when the fetch fails — so the gate sent them to `/login` exactly when
 * they were being used as intended. Each of them already calls `notFound()`
 * in production, so skipping the check here opens nothing in a deployment.
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
    void getSession().then((user) => {
      if (!current) return;
      if (user || hasDevToken) {
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
