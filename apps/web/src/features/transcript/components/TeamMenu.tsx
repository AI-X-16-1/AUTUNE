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

import { NEW_TEAM_ROWS, NewTeamWindow } from "./NewTeamWindow";
import { besideSidebar, TeamsWindow, type Place } from "./TeamsWindow";

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
 * One team: its name, with nothing to choose. None, or a list that could not
 * be read: nothing -- the screens say so themselves, and a sidebar that
 * showed an error on every page would say it louder than it deserves.
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
 * meetings** (the user: "회의 상태에서 사이드바에 다른 팀 누르면 해당 팀의
 * 회의로 이동"). A meeting's own screens show one team's meeting and have no
 * row that could follow the choice, so the page used to stay on a meeting of
 * the team just left. Now the choice is kept and the app goes to the home
 * screen showing that team (`/?team=`). The team already marked does nothing
 * new, as before.
 *
 * **"+" beside the heading makes a team** (the user: "팀 옆에 + 버튼으로
 * 팀생성하면서 구성원들에게 메일을 보내거나 초대링크를 생성하게 작은 화면").
 * It opens `NewTeamWindow` -- S02's two steps in a window -- for anybody who
 * has this menu, a person on one team included. Only one of the two windows
 * is open at a time.
 *
 * Not on "회의 시작" (`/meetings/new`): a recording or an upload may be in
 * progress there, and a press in the sidebar must not drop it. There the
 * choice changes and the page stays, as it did. Whether to ask first and go
 * is the module owner's to settle. On a meeting's review screens the move is
 * what any other sidebar entry already does.
 */

/** A meeting's own screens: `/meetings/<id>` and its tabs, never "회의 시작". */
function insideAMeeting(pathname: string | null): boolean {
  return (
    pathname !== null && pathname.startsWith("/meetings/") && !pathname.startsWith("/meetings/new")
  );
}

export function TeamMenu() {
  const [teams, setTeams] = useState<TeamSummary[]>([]);
  const [teamId, setTeamId] = useState<string | null>(null);
  const [at, setAt] = useState<Place | null>(null);
  const [making, setMaking] = useState<Place | null>(null);
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
  const closeMaking = useCallback(() => setMaking(null), []);

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
        aria-expanded={making !== null}
        aria-label="새 팀 만들기"
        onClick={() => {
          setAt(null);
          setMaking((open) =>
            open === null && plus.current ? besideSidebar(plus.current, NEW_TEAM_ROWS) : null,
          );
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
      {making !== null ? (
        <NewTeamWindow at={making} opener={plus} onClose={closeMaking} />
      ) : null}
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

  const choose = (chosenId: string) => {
    const another = chosenId !== teamId;
    rememberTeam(chosenId);
    if (another && insideAMeeting(pathname)) router.push(`/?team=${encodeURIComponent(chosenId)}`);
  };

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
          setMaking(null);
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
