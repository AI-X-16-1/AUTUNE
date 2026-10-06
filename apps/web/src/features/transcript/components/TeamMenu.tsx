"use client";

import { useEffect, useState } from "react";

import { listTeams } from "../api";
import { onTeamChosen, rememberTeam, teamToOpen } from "../selectedTeam";
import type { TeamSummary } from "../types";

/**
 * The team a person is looking at, chosen from the sidebar (the user,
 * 2026-10-06).
 *
 * Since #867 the team chosen on one screen is the team on the next, but the
 * only place to choose it was the row at the top of each team-level screen:
 * on a screen without that row there was nothing to press. This is the same
 * choice, in the one place that is on every screen. Picking a team here is
 * `rememberTeam`, which every mounted `TeamScope` follows at once; picking one
 * in a row moves the mark here.
 *
 * The rows stay where they are, and pinning stays in them: this lists and
 * chooses, in the order `GET /teams` gives (pinned first). A row that pins or
 * unpins reorders itself; this keeps the order it loaded with until the next
 * load.
 *
 * One team: its name, with nothing to choose. None, or a list that could not
 * be read: nothing -- the screens say so themselves, and a sidebar that
 * showed an error on every page would say it louder than it deserves.
 *
 * **At most three teams are listed** (the user, 2026-10-06: "팀 고정한거
 * 포함해서 3개만"). They are the first three of the order above, so pinned
 * teams -- of which a person may have three -- come before any other and
 * fill the list when there are three of them. A sidebar is on every screen
 * and a long list there pushes the menu below it out of reach; the full
 * list is the row at the top of each team-level screen, which stays.
 *
 * The team being looked at is always one of the three. When it is not among
 * the first three -- chosen in a screen's row, or remembered from before --
 * it takes the last place, because a menu that marked nothing would leave a
 * person unable to tell from the sidebar which team's data they are reading.
 * (Ours to decide; the order said three and no more.)
 */
export const MENU_TEAMS = 3;

function listed(teams: TeamSummary[], teamId: string | null): TeamSummary[] {
  const first = teams.slice(0, MENU_TEAMS);
  if (teamId === null || first.some((team) => team.team_id === teamId)) return first;
  const looking = teams.find((team) => team.team_id === teamId);
  return looking ? [...first.slice(0, MENU_TEAMS - 1), looking] : first;
}

export function TeamMenu() {
  const [teams, setTeams] = useState<TeamSummary[]>([]);
  const [teamId, setTeamId] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    listTeams()
      .then((list) => {
        if (!current) return;
        setTeams(list);
        setTeamId(teamToOpen(list));
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, []);

  useEffect(() => onTeamChosen(setTeamId), []);

  if (teams.length === 0) return null;

  const heading = (
    <div
      className="text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-metaSmall)", fontWeight: 600, padding: "2px 0 4px" }}
    >
      팀
    </div>
  );
  const text = { fontSize: "var(--control-text-default)", fontWeight: 500 } as const;

  if (teams.length === 1)
    return (
      <div>
        {heading}
        <div className="truncate text-[var(--color-ink-body)]" style={{ ...text, padding: "6px 0" }}>
          {teams[0]?.name}
        </div>
      </div>
    );

  return (
    <nav aria-label="팀" className="flex flex-col">
      {heading}
      {listed(teams, teamId).map((team) => {
        const chosen = team.team_id === teamId;
        return (
          <button
            key={team.team_id}
            type="button"
            aria-pressed={chosen}
            onClick={() => rememberTeam(team.team_id)}
            className={
              chosen
                ? "truncate rounded-[var(--radius)] bg-[var(--color-accent-selection)] text-left text-[var(--color-accent-hover)]"
                : "truncate text-left text-[var(--color-ink-body)] hover:text-[var(--color-ink-strong)]"
            }
            style={
              chosen
                ? { ...text, fontWeight: 600, padding: "6px 10px", margin: "0 -10px" }
                : { ...text, padding: "6px 0" }
            }
          >
            {team.name}
          </button>
        );
      })}
    </nav>
  );
}
