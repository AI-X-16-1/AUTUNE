"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { Assistant } from "@/features/agent";
import { onTeamChosen, rememberedTeam } from "@/features/transcript";

import { useSessionUser } from "./SessionGate";

/**
 * Mounts S34, the assistant, once for every screen in the app shell.
 *
 * Assembly only: who is signed in and which page is open come from the shell,
 * and the screen is the agent feature's. Nothing is drawn without a session or
 * a team — a developer token has neither, and a turn needs a team.
 *
 * Team questions go to the team chosen in the sidebar's menu (#1055), the
 * value `features/transcript` keeps for every team-level screen, exactly as
 * `materials/page.tsx` reads it: the remembered team while the person is still
 * on it, else the first, and a new choice as soon as it is made. On a meeting
 * page the meeting names its own team.
 */
export function AssistantMount() {
  const pathname = usePathname();
  const user = useSessionUser();
  // Read after mount: the store is the browser's, and the first render has to
  // match the server's, which has none.
  const [chosen, setChosen] = useState<string | null>(null);
  useEffect(() => {
    setChosen(rememberedTeam());
    return onTeamChosen(setChosen);
  }, []);
  const team = user?.teams.find((t) => t.id === chosen) ?? user?.teams[0];
  if (!user || !team) return null;
  return (
    <Assistant
      teamId={team.id}
      teamName={team.name}
      userName={user.display_name}
      pathname={pathname}
    />
  );
}
