"use client";

import { useEffect, useRef, useState } from "react";

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
 * and a long list there pushes the menu below it out of reach.
 *
 * **One control under the three shows the rest, in a small window of its
 * own** (the user, the same day: "누르면 남은 팀들 보이게", then "사이드바에
 * 직접 늘리지 말고 작은 화면 띄워서"). It says how many more there are and
 * opens them beside itself, over the page: the sidebar does not grow, so
 * nothing under the team section moves. The window closes when a team is
 * picked -- which then takes the last of the three places -- when the
 * control is pressed again, on Escape, and on a press anywhere outside it.
 * It lists only the teams that are not already in the sidebar, and is absent
 * when there are none. The row at the top of each team-level screen still
 * lists every team, and stays.
 *
 * Placed with `position: fixed` from where the control is when it opens: the
 * sidebar scrolls and clips, and a child positioned inside it would be cut
 * off at its edge. It starts just past the sidebar's edge (the nearest
 * `aside`), so it lies on the page and not across the two.
 *
 * The team being looked at is always one of the three. When it is not among
 * the first three -- chosen in a screen's row, or remembered from before --
 * it takes the last place, because a menu that marked nothing would leave a
 * person unable to tell from the sidebar which team's data they are reading.
 * (Ours to decide; the order said three and no more.)
 */
export const MENU_TEAMS = 3;

/** The small window scrolls past this height rather than grow down the page. */
const REST_MAX_HEIGHT = 320;

function listed(teams: TeamSummary[], teamId: string | null): TeamSummary[] {
  const first = teams.slice(0, MENU_TEAMS);
  if (teamId === null || first.some((team) => team.team_id === teamId)) return first;
  const looking = teams.find((team) => team.team_id === teamId);
  return looking ? [...first.slice(0, MENU_TEAMS - 1), looking] : first;
}

export function TeamMenu() {
  const [teams, setTeams] = useState<TeamSummary[]>([]);
  const [teamId, setTeamId] = useState<string | null>(null);
  const [showingRest, setShowingRest] = useState(false);
  const [at, setAt] = useState({ left: 0, top: 0 });
  const control = useRef<HTMLButtonElement>(null);
  const window_ = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!showingRest) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setShowingRest(false);
    };
    const onPress = (event: MouseEvent) => {
      const target = event.target as Node;
      if (window_.current?.contains(target) || control.current?.contains(target)) return;
      setShowingRest(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPress);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPress);
    };
  }, [showingRest]);

  const toggleRest = () => {
    const box = control.current?.getBoundingClientRect();
    // Clear of the sidebar's own edge, not only of the control: the control
    // ends short of that edge by the sidebar's padding, and a window placed
    // from the control alone sat half on the sidebar (seen in a browser).
    const edge = control.current?.closest("aside")?.getBoundingClientRect().right ?? 0;
    // Level with the control, unless that would run it off the bottom of the
    // page: then as low as it fits. The height is an estimate (a row is about
    // 34px), which is enough to keep the last team in reach.
    const more = Math.max(0, teams.length - MENU_TEAMS);
    const tall = Math.min(REST_MAX_HEIGHT, more * 34 + 18);
    const top = box ? Math.max(8, Math.min(box.top, window.innerHeight - tall - 8)) : 0;
    if (box) setAt({ left: Math.max(box.right + 12, edge + 8), top });
    setShowingRest((open) => !open);
  };

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

  const three = listed(teams, teamId);
  const rest = teams.filter((team) => !three.includes(team));

  return (
    <nav aria-label="팀" className="flex flex-col">
      {heading}
      {three.map((team) => {
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
      {rest.length > 0 ? (
        <>
          <button
            ref={control}
            type="button"
            aria-haspopup="dialog"
            aria-expanded={showingRest}
            aria-controls="team-menu-rest"
            onClick={toggleRest}
            className="text-left text-[var(--color-ink-muted)] hover:text-[var(--color-ink-strong)]"
            style={{ fontSize: "var(--text-metaSmall)", fontWeight: 500, padding: "6px 0" }}
          >
            다른 팀 {rest.length}개
          </button>
          {showingRest ? (
            <div
              ref={window_}
              id="team-menu-rest"
              role="dialog"
              aria-label="다른 팀"
              className="flex flex-col rounded-[var(--radius)] border border-[var(--color-hairline)] bg-[var(--color-paper)]"
              style={{
                position: "fixed",
                left: at.left,
                top: at.top,
                zIndex: 50,
                minWidth: 200,
                maxWidth: 280,
                maxHeight: REST_MAX_HEIGHT,
                overflowY: "auto",
                padding: "8px 14px",
                boxShadow: "var(--shadow-overlay)",
              }}
            >
              {rest.map((team) => (
                <button
                  key={team.team_id}
                  type="button"
                  aria-pressed={false}
                  onClick={() => {
                    rememberTeam(team.team_id);
                    setShowingRest(false);
                  }}
                  className="truncate text-left text-[var(--color-ink-body)] hover:text-[var(--color-ink-strong)]"
                  style={{ ...text, padding: "6px 0" }}
                >
                  {team.name}
                </button>
              ))}
            </div>
          ) : null}
        </>
      ) : null}
    </nav>
  );
}
