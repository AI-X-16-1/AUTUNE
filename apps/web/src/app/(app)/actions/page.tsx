"use client";

import { TeamActionsScreen } from "@/features/actions";

import { useSessionUser } from "../../_components/SessionGate";

/**
 * S17 across every meeting the caller's teams hold — the sidebar's "액션아이템".
 *
 * Assembly only: the board is module B's. The route hands it the signed-in
 * person's id for the "내 담당" tab, because the session is the shell's and a
 * feature does not read it. Null on a developer token with no session; the
 * screen then leaves that tab out.
 */
export default function ActionsPage() {
  const user = useSessionUser();
  return <TeamActionsScreen me={user?.id ?? null} />;
}
