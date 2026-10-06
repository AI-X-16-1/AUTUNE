/**
 * The team a person last chose, kept in this browser.
 *
 * Every team-level screen mounts its own `TeamScope`, so a choice held only in
 * that component's state was gone at the next menu: the dashboard opened on
 * the first team again after somebody had picked the second on the decisions
 * screen. The choice is kept here until the person makes another.
 *
 * In the browser and not on the account, unlike a pin: it is "where I was
 * looking", and a second device may well be looking somewhere else. Only an
 * id is stored. A browser that refuses storage (private mode, a blocked site)
 * simply forgets, which is what happened before.
 */
const KEY = "autune.team";

export function rememberedTeam(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function rememberTeam(teamId: string): void {
  try {
    window.localStorage.setItem(KEY, teamId);
  } catch {
    // Nothing to do: the choice lasts as long as this screen does.
  }
}

/**
 * The team to open on: the remembered one while the person is still on it,
 * else the first of the list (pinned teams come first). A team they have left
 * is not in the list, so it is passed over without being asked for.
 */
export function teamToOpen(teams: readonly { team_id: string }[]): string | null {
  const kept = rememberedTeam();
  if (kept !== null && teams.some((team) => team.team_id === kept)) return kept;
  return teams[0]?.team_id ?? null;
}
