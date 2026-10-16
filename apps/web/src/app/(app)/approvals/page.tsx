"use client";

import { useEffect, useState } from "react";

import { ApprovalsScreen } from "@/features/agent";
import { onTeamChosen, rememberedTeam } from "@/features/transcript";

import { useSessionUser } from "../../_components/SessionGate";

/**
 * 승인 대기 — assembly only: the screen lives in `features/agent`.
 *
 * It lists the team chosen in the sidebar's menu (`features/transcript`,
 * module A's) and follows a new choice while it is open, as
 * `materials/page.tsx` does; the two features do not import each other.
 *
 * With no choice remembered, or one for a team the person has left, it opens
 * on their first team, as `AssistantMount` does. The teams come from the
 * session, which is read before any screen is drawn, so the screen never
 * waits on the sidebar's own list -- which, if it failed, left this page on
 * "불러오는 중…" for good (#1322 review). A person on no team is told so.
 */
export default function ApprovalsPage() {
  const user = useSessionUser();
  // Read after mount: the store is the browser's, and the first render has to
  // match the server's, which has none. `ready` keeps the first team from
  // being read in the moment before the remembered one is.
  const [ready, setReady] = useState(false);
  const [chosen, setChosen] = useState<string | null>(null);
  useEffect(() => {
    setChosen(rememberedTeam());
    setReady(true);
    return onTeamChosen(setChosen);
  }, []);
  const teams = user?.teams ?? [];
  const team = teams.find((t) => t.id === chosen) ?? teams[0];
  return <ApprovalsScreen teamId={ready ? (team?.id ?? null) : undefined} />;
}
