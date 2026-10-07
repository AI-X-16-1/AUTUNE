"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { ApiError } from "@/shared/api/client";
import { Button, ChipToggle } from "@/shared/ui";

import { listTeams, pinTeam, unpinTeam } from "../api";
import {
  announceTeams,
  LISTED_TEAMS,
  onTeamChosen,
  onTeamsChanged,
  rememberTeam,
  teamsToList,
  teamToOpen,
} from "../selectedTeam";
import type { TeamSummary } from "../types";

import { below, TeamsWindow, type Place } from "./TeamsWindow";

/**
 * Which team a team-level screen is about — the one thing module A knows that
 * those screens need and cannot ask for themselves.
 *
 * S22 (decision lineage) and S26 (dashboard) take a `teamId`, and until now
 * the only way to hand them one was to type it into a dev page's query string.
 * Teams and memberships are A's to write (invariant 4), and `GET
 * /api/audio/teams` already answers "which teams may this person see", so the
 * answer comes from here and is passed down as a plain string. The screen
 * below never imports this feature; the route composes the two.
 *
 * One team: rendered straight through. Several: a row of chips above the
 * screen. None: said in words, because a screen asked for a team that does
 * not exist would only show an error of its own.
 *
 * **The row shows three teams, and "더보기" for the rest** (the user,
 * 2026-10-07: "내부의 팀 목록들도 사이드바처럼 3개만 보이고 더보기로"). They are
 * the sidebar's three (`teamsToList`): the first three as the server orders
 * them -- pinned first, then in the order joined -- with the team on screen
 * always one of them, in the last place when it is not among the first.
 * "더보기" opens the window that lists every team (`TeamsWindow`), the same
 * one the sidebar and the home screen open; a team picked there is the team
 * on screen. It is offered only to somebody on more than three teams: with
 * three or fewer the row already shows them all, and pinning has its own
 * button here.
 *
 * **A team once chosen stays chosen** (the user, 2026-10-06): on this screen
 * and on every other one that mounts this row, until the person picks another
 * (`selectedTeam`). Until they have picked one, the first of the list is shown.
 *
 * **The row is also where a team is pinned** (the user, 2026-10-02). The
 * list comes pinned teams first, then in the order joined, and the first is
 * the default here and on every other screen for somebody who has not chosen
 * a team in this browser. Somebody on several teams can
 * pin up to three, so the default is theirs to choose and not an accident of
 * which team they joined first. The pin is stored on the account, so it is
 * the same on every device, and it is one person's: nobody else sees it.
 * Pinning does not change which team this screen is showing.
 */
export function TeamScope({ children }: { children: (teamId: string) => ReactNode }) {
  const [teams, setTeams] = useState<TeamSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [teamId, setTeamId] = useState<string | null>(null);
  const [pinning, setPinning] = useState(false);
  const [pinProblem, setPinProblem] = useState<string | null>(null);
  const [at, setAt] = useState<Place | null>(null);
  const more = useRef<HTMLSpanElement>(null);
  const close = useCallback(() => setAt(null), []);

  const togglePin = async (team: TeamSummary) => {
    setPinning(true);
    setPinProblem(null);
    try {
      // The answer is the list in its new order; the team on screen stays.
      // Told to every list of teams on the page, this one among them.
      announceTeams(await (team.pinned ? unpinTeam(team.team_id) : pinTeam(team.team_id)));
    } catch (caught) {
      setPinProblem(
        caught instanceof ApiError && caught.code === "too_many_pinned_teams"
          ? "팀은 3개까지 고정할 수 있습니다. 다른 팀의 고정을 풀고 다시 시도해 주세요."
          : "고정을 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.",
      );
    } finally {
      setPinning(false);
    }
  };

  useEffect(() => {
    let current = true;
    listTeams()
      .then((list) => {
        if (!current) return;
        setTeams(list);
        setTeamId(teamToOpen(list));
      })
      .catch((caught: unknown) => {
        if (current) setError(caught instanceof Error ? caught.message : "팀 목록을 불러오지 못했습니다.");
      });
    return () => {
      current = false;
    };
  }, []);

  // A team chosen elsewhere on the page -- the sidebar's menu -- is this
  // screen's team at once. The row's own click goes the same way round.
  useEffect(() => onTeamChosen(setTeamId), []);
  // A pin made here, in the sidebar's window or in another row reorders this
  // list; the team on screen stays.
  useEffect(() => onTeamsChanged<TeamSummary>(setTeams), []);

  const muted = { fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" } as const;

  if (error)
    return (
      <p role="alert" style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-critical)" }}>
        {error}
      </p>
    );
  if (teams === null) return <p style={muted}>팀을 불러오는 중입니다…</p>;
  if (teams.length === 0 || teamId === null) return <p style={muted}>속한 팀이 없습니다.</p>;

  // A team chosen elsewhere that is not in this list (it cannot be: both
  // read the same teams) would show nothing, so fall back to the first.
  const current = teams.find((team) => team.team_id === teamId) ?? teams[0];

  return (
    <>
      {teams.length > 1 && (
        <div style={{ marginBottom: "var(--space-16)" }}>
          <div className="flex flex-wrap items-center gap-1.5">
            {teamsToList(teams, teamId).map((team) => (
              <ChipToggle
                key={team.team_id}
                selected={team.team_id === teamId}
                onClick={() => rememberTeam(team.team_id)}
              >
                {team.pinned ? `${team.name} · 고정` : team.name}
              </ChipToggle>
            ))}
            {teams.length > LISTED_TEAMS ? (
              <span ref={more}>
                <Button
                  tone="text"
                  size="compact"
                  type="button"
                  aria-haspopup="dialog"
                  aria-expanded={at !== null}
                  aria-controls="teams-window"
                  aria-label="팀 더보기"
                  onClick={() =>
                    setAt((open) =>
                      open === null && more.current ? below(more.current, teams.length) : null,
                    )
                  }
                >
                  더보기
                </Button>
              </span>
            ) : null}
            {at !== null ? (
              <TeamsWindow
                teams={teams}
                teamId={teamId}
                at={at}
                opener={more}
                onChoose={(chosenId) => {
                  rememberTeam(chosenId);
                  close();
                }}
                onClose={close}
              />
            ) : null}
            {current ? (
              <Button
                tone="text"
                size="compact"
                type="button"
                disabled={pinning}
                onClick={() => void togglePin(current)}
              >
                {current.pinned ? "고정 해제" : "맨 위에 고정"}
              </Button>
            ) : null}
          </div>
          {pinProblem !== null ? (
            <p role="alert" className="mt-1" style={{ ...muted, color: "var(--color-signal-critical)" }}>
              {pinProblem}
            </p>
          ) : null}
        </div>
      )}
      {children(teamId)}
    </>
  );
}
