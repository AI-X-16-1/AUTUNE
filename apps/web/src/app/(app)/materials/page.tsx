"use client";

import { useEffect, useState } from "react";

import { MaterialsScreen } from "@/features/actions";
import { onTeamChosen, rememberedTeam, rememberTeam } from "@/features/transcript";

/**
 * The sidebar's "자료" (#817): the Google Drive files a team keeps, by link.
 *
 * Assembly only: the screen is `features/actions`, module B's, and this file
 * exists because a feature cannot give itself a route.
 *
 * It also joins that screen to the team a person chose elsewhere -- the
 * sidebar's menu (`features/transcript`, module A's) -- exactly as
 * `settings/integrations/page.tsx` does: the screen opens on that team,
 * follows a new choice while it is open, and a team picked on it is the choice
 * everywhere else. The two features do not import each other.
 */
export default function MaterialsPage() {
  // Read after mount: the store is the browser's, and the first render has to
  // match the server's, which has none.
  const [chosen, setChosen] = useState<string | null>(null);
  useEffect(() => {
    setChosen(rememberedTeam());
    return onTeamChosen(setChosen);
  }, []);
  return <MaterialsScreen chosenTeamId={chosen} onChooseTeam={rememberTeam} />;
}
