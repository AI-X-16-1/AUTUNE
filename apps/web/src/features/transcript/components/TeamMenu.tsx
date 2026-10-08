"use client";

import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { listTeams } from "../api";
import {
  onTeamChosen,
  onTeamsChanged,
  rememberTeam,
  teamToOpen,
  teamsToList,
} from "../selectedTeam";
import type { TeamSummary } from "../types";

import { NewTeamWindow } from "./NewTeamWindow";
import { besideSidebar, TeamsWindow, type Place } from "./TeamsWindow";

/**
 * The team a person is looking at, chosen from the sidebar (the user,
 * 2026-10-06).
 *
 * Since #867 the team chosen on one screen is the team on the next, but the
 * only place to choose it was the row at the top of each team-level screen:
 * on a screen without that row there was nothing to press. This is the same
 * choice, in the one place that is on every screen. Picking a team here is
 * `rememberTeam`; picking one in a screen's row moves the mark here.
 *
 * **A team name pressed here opens that team's home screen, from any screen**
 * (the user, 2026-10-08: "어떤 화면이던 사이드 바에 팀명을 누르면 해당 팀의
 * 홈화면 출력"). Until then the screen on show followed the choice in place
 * and only a meeting's screens moved. Now the choice is kept and the app goes
 * to the home screen showing that team (`/?team=`) -- from a team-level
 * screen, a meeting's screens, the settings: every screen that has this
 * sidebar. (The legal page and a live meeting are drawn without it.) The
 * sentence has no exception in it, so these are read into it and are ours:
 *
 * - The team already marked goes home too. It is the way back to the team's
 *   home from wherever a person is.
 * - On the home screen nothing is pushed: it already follows the choice and
 *   writes the team into its own address (`HomeScreen`), so a press there
 *   shows the team at once, and a press on the team already shown changes
 *   nothing.
 * - A team picked in "더보기"'s window is the same press. A pin made there
 *   goes nowhere.
 * - The row at the top of a team-level screen is not the sidebar: it still
 *   chooses in place (`TeamScope`).
 * - Leaving a screen this way asks nothing, as leaving it by "홈" or any other
 *   sidebar entry asks nothing. The one screen kept out is "회의 시작", below.
 *
 * One team: its name, which is the same way home, with nothing to choose.
 * None, or a list that could not be read: nothing -- the screens say so
 * themselves, and a sidebar that showed an error on every page would say it
 * louder than it deserves.
 *
 * **At most three teams are listed** (the user: "팀 고정한거 포함해서 3개만").
 * They are the first three of the order `GET /teams` gives, so pinned teams
 * -- of which a person may have three -- come before any other and fill the
 * list when there are three of them. A sidebar is on every screen and a long
 * list there pushes the menu below it out of reach. The team being looked at
 * is always one of the three (`teamsToList`); that part is ours to decide.
 *
 * **"더보기" under them opens every team in a small window** (the user:
 * "누르면 남은 팀들 보이게", "사이드바에 직접 늘리지 말고 작은 화면 띄워서",
 * and, with the home screen's row, "맨위 고정도 거기로 이동"). The sidebar
 * does not grow, so nothing under the team section moves. The window lists
 * all of a person's teams -- the three here among them -- because it is also
 * where a team is pinned, and a pinned team is one of these three
 * (`TeamsWindow`). It is offered to anybody on more than one team.
 *
 * **Inside a meeting, another team pressed here goes to that team's
 * meetings** (the user, 2026-10-06: "회의 상태에서 사이드바에 다른 팀 누르면
 * 해당 팀의 회의로 이동"). This was the first screen to move, because a
 * meeting's own screens have no row that could follow the choice. The
 * sentence of 2026-10-08 above made it the rule for every screen and for the
 * marked team as well.
 *
 * **"+" beside the heading makes a team** (the user: "팀 옆에 + 버튼으로
 * 팀생성하면서 구성원들에게 메일을 보내거나 초대링크를 생성하게 작은 화면").
 * It opens `NewTeamWindow` -- S02's two steps in a window -- for anybody who
 * has this menu, a person on one team included. That window opens in the
 * middle of the screen over a dimmed page, as an action item's does (the
 * user, 2026-10-07); "더보기"'s stays beside the sidebar. Only one of the two
 * windows is open at a time.
 *
 * Not on "회의 시작" (`/meetings/new`): a recording or an upload may be in
 * progress there, and a press in the sidebar must not drop it. There the
 * choice changes and the page stays, as it did before 2026-10-08 and still
 * does -- "어떤 화면이던" was not asked about a recording in progress, and
 * until it is, the press that could lose one is the one not taken. Whether to
 * ask first and go is the module owner's to settle.
 */

/**
 * Whether a press on a team name leaves the screen on show as it is: the home
 * screen, which follows the choice by itself, and "회의 시작", which may hold
 * a recording or an upload.
 */
function staysWhereItIs(pathname: string | null): boolean {
  return pathname === "/" || (pathname !== null && pathname.startsWith("/meetings/new"));
}

export function TeamMenu() {
  const [teams, setTeams] = useState<TeamSummary[]>([]);
  const [teamId, setTeamId] = useState<string | null>(null);
  const [at, setAt] = useState<Place | null>(null);
  const [making, setMaking] = useState(false);
  const pathname = usePathname();
  const router = useRouter();
  const control = useRef<HTMLButtonElement>(null);
  const plus = useRef<HTMLButtonElement>(null);

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
  // A pin made anywhere on the page reorders this list too.
  useEffect(() => onTeamsChanged<TeamSummary>(setTeams), []);

  const close = useCallback(() => setAt(null), []);
  const closeMaking = useCallback(() => setMaking(false), []);

  if (teams.length === 0) return null;

  const heading = (
    <div
      className="flex items-center justify-between text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-metaSmall)", fontWeight: 600, padding: "2px 0 4px" }}
    >
      <span>팀</span>
      <button
        ref={plus}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={making}
        aria-label="새 팀 만들기"
        onClick={() => {
          setAt(null);
          setMaking((open) => !open);
        }}
        className="hover:text-[var(--color-ink-strong)]"
        // A 12px glyph is too small a thing to press: the padding makes it
        // 24px, and the negative margin keeps the heading's own height.
        style={{
          fontSize: "var(--control-text-default)",
          lineHeight: 1,
          padding: "6px 7px",
          margin: "-6px -7px",
        }}
      >
        +
      </button>
      {making ? <NewTeamWindow opener={plus} onClose={closeMaking} /> : null}
    </div>
  );
  const text = { fontSize: "var(--control-text-default)", fontWeight: 500 } as const;

  const choose = (chosenId: string) => {
    rememberTeam(chosenId);
    if (!staysWhereItIs(pathname)) router.push(`/?team=${encodeURIComponent(chosenId)}`);
  };

  const only = teams.length === 1 ? teams[0] : undefined;
  if (only !== undefined)
    return (
      <div className="flex flex-col">
        {heading}
        <button
          type="button"
          onClick={() => choose(only.team_id)}
          className="truncate text-left text-[var(--color-ink-body)] hover:text-[var(--color-ink-strong)]"
          style={{ ...text, padding: "6px 0" }}
        >
          {only.name}
        </button>
      </div>
    );

  return (
    <nav aria-label="팀" className="flex flex-col">
      {heading}
      {teamsToList(teams, teamId).map((team) => {
        const chosen = team.team_id === teamId;
        return (
          <button
            key={team.team_id}
            type="button"
            aria-pressed={chosen}
            onClick={() => choose(team.team_id)}
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
      <button
        ref={control}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={at !== null}
        aria-controls="teams-window"
        aria-label="팀 더보기"
        onClick={() => {
          setMaking(false);
          setAt((open) =>
            open === null && control.current ? besideSidebar(control.current, teams.length) : null,
          );
        }}
        className="text-left text-[var(--color-ink-muted)] hover:text-[var(--color-ink-strong)]"
        style={{ fontSize: "var(--text-metaSmall)", fontWeight: 500, padding: "6px 0" }}
      >
        더보기
      </button>
      {at !== null ? (
        <TeamsWindow
          teams={teams}
          teamId={teamId}
          at={at}
          opener={control}
          onChoose={(chosenId) => {
            choose(chosenId);
            close();
          }}
          onClose={close}
        />
      ) : null}
    </nav>
  );
}
