"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { listTeamGaps } from "../api";
import type { GapSeverity, TeamGap } from "../types";

const HIGH: readonly GapSeverity[] = ["high"];
const ALL: readonly GapSeverity[] = ["high", "medium", "low"];

/** What the last settled request left behind, and which question it answered. */
interface State {
  key: string;
  gaps: TeamGap[] | null;
  error: Error | null;
  loading: boolean;
}

/**
 * A team's open gaps across its meetings (#550). HIGH alone unless `showAll`.
 *
 * Every piece of state carries the team and the severities it was asked for,
 * as `useGapReport` carries its meeting: flipping the toggle twice puts two
 * requests in flight, and the first to answer is not the one asked for last.
 */
export function useTeamGaps(teamId: string, showAll: boolean) {
  const key = `${teamId}:${showAll ? "all" : "high"}`;
  const [state, setState] = useState<State>({ key, gaps: null, error: null, loading: true });
  const latest = useRef(0);

  const reload = useCallback(async () => {
    const ticket = latest.current + 1;
    latest.current = ticket;
    setState((previous) => ({ ...previous, key, loading: true, error: null }));
    try {
      const gaps = await listTeamGaps(teamId, showAll ? ALL : HIGH);
      if (ticket === latest.current) setState({ key, gaps, error: null, loading: false });
    } catch (cause) {
      const error = cause instanceof Error ? cause : new Error(String(cause));
      if (ticket === latest.current) setState({ key, gaps: null, error, loading: false });
    }
  }, [key, teamId, showAll]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (state.key !== key) return { gaps: null, loading: true, error: null, reload };
  return { gaps: state.gaps, loading: state.loading, error: state.error, reload };
}
