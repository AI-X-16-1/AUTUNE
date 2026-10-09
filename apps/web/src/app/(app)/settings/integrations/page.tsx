"use client";

import { useEffect, useState } from "react";

import { IntegrationSettingsScreen } from "@/features/actions";
import { onTeamChosen, rememberedTeam, rememberTeam } from "@/features/transcript";

/**
 * S28, 설정 › 연동 (#496).
 *
 * Assembly only: the screen and the connect buttons it gathers live in
 * `features/actions`, module B's, which built them for the 할 일 tab. This file
 * exists because a feature cannot give itself a route.
 *
 * It also joins that screen to the team a person chose elsewhere -- the
 * sidebar's menu, another screen's row (`features/transcript`, module A's):
 * the screen opens on that team, follows a new choice while it is open, and
 * a team picked on it is the choice everywhere else. The two features do not
 * import each other; this is the one place that knows both.
 */
export default function IntegrationSettingsPage() {
  // Read after mount: the store is the browser's, and the first render has to
  // match the server's, which has none.
  const [chosen, setChosen] = useState<string | null>(null);
  useEffect(() => {
    setChosen(rememberedTeam());
    return onTeamChosen(setChosen);
  }, []);
  return <IntegrationSettingsScreen chosenTeamId={chosen} onChooseTeam={rememberTeam} />;
}
