"use client";

import { usePathname } from "next/navigation";

import { Assistant } from "@/features/agent";

import { useSessionUser } from "./SessionGate";

/**
 * Mounts S34, the assistant, once for every screen in the app shell.
 *
 * Assembly only: who is signed in and which page is open come from the shell,
 * and the screen is the agent feature's. Nothing is drawn without a session or
 * a team — a developer token has neither, and a turn needs a team. With
 * several teams the first is used, as S28 does until a team switcher exists.
 */
export function AssistantMount() {
  const pathname = usePathname();
  const user = useSessionUser();
  const team = user?.teams[0];
  if (!user || !team) return null;
  return (
    <Assistant
      teamId={team.id}
      userName={user.display_name}
      pathname={pathname}
    />
  );
}
