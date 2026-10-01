"use client";

import { useEffect, useState, type ReactNode } from "react";

import { ChipToggle } from "@/shared/ui";

import { listTeams } from "../api";
import type { TeamSummary } from "../types";

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
 * screen, first team selected. None: said in words, because a screen asked for
 * a team that does not exist would only show an error of its own.
 */
export function TeamScope({ children }: { children: (teamId: string) => ReactNode }) {
  const [teams, setTeams] = useState<TeamSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [teamId, setTeamId] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    listTeams()
      .then((list) => {
        if (!current) return;
        setTeams(list);
        setTeamId(list[0]?.team_id ?? null);
      })
      .catch((caught: unknown) => {
        if (current) setError(caught instanceof Error ? caught.message : "팀 목록을 불러오지 못했습니다.");
      });
    return () => {
      current = false;
    };
  }, []);

  const muted = { fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" } as const;

  if (error)
    return (
      <p role="alert" style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-critical)" }}>
        {error}
      </p>
    );
  if (teams === null) return <p style={muted}>팀을 불러오는 중입니다…</p>;
  if (teams.length === 0 || teamId === null) return <p style={muted}>속한 팀이 없습니다.</p>;

  return (
    <>
      {teams.length > 1 && (
        <div className="flex flex-wrap gap-1.5" style={{ marginBottom: "var(--space-16)" }}>
          {teams.map((team) => (
            <ChipToggle
              key={team.team_id}
              selected={team.team_id === teamId}
              onClick={() => setTeamId(team.team_id)}
            >
              {team.name}
            </ChipToggle>
          ))}
        </div>
      )}
      {children(teamId)}
    </>
  );
}
