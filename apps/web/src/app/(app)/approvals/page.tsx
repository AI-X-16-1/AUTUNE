"use client";

import { useEffect, useState } from "react";

import { ApprovalsScreen } from "@/features/agent";
import { onTeamChosen, rememberedTeam } from "@/features/transcript";

/**
 * 승인 대기 — assembly only: the screen lives in `features/agent`.
 *
 * It lists the team chosen in the sidebar's menu (`features/transcript`,
 * module A's) and follows a new choice while it is open, as
 * `materials/page.tsx` does; the two features do not import each other.
 */
export default function ApprovalsPage() {
  // Read after mount: the store is the browser's, and the first render has to
  // match the server's, which has none.
  const [chosen, setChosen] = useState<string | null>(null);
  useEffect(() => {
    setChosen(rememberedTeam());
    return onTeamChosen(setChosen);
  }, []);
  return <ApprovalsScreen teamId={chosen} />;
}
