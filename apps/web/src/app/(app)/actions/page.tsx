"use client";

import { useEffect, useState } from "react";

import { TeamActionsScreen } from "@/features/actions";
import { onTeamChosen } from "@/features/transcript";

import { useSessionUser } from "../../_components/SessionGate";

/**
 * S17 across every meeting the caller's teams hold — the sidebar's "할 일".
 *
 * Assembly only: the board is module B's. The route hands it the signed-in
 * person's id for the "내 담당" tab, because the session is the shell's and a
 * feature does not read it. Null on a developer token with no session; the
 * screen then leaves that tab out.
 *
 * It also hands the board the team pressed in the sidebar's menu
 * (`features/transcript`, module A's) while this screen is open (the user,
 * 2026-10-08), as `materials/page.tsx` joins its screen to that menu: the two
 * features do not import each other. The board opens on every team -- the
 * team remembered from another screen is not read -- and shows one team from
 * the moment its name is pressed until "전체 보기" or the screen is left.
 */
export default function ActionsPage() {
  const user = useSessionUser();
  const [pressed, setPressed] = useState<string | null>(null);
  useEffect(() => onTeamChosen(setPressed), []);
  return (
    <TeamActionsScreen
      me={user?.id ?? null}
      teamId={pressed}
      onEveryTeam={() => setPressed(null)}
    />
  );
}
