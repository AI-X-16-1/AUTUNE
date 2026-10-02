"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getExplanations } from "../api";
import type { GapExplanations } from "../types";

interface State {
  meetingId: string;
  explanations: GapExplanations | null;
  error: Error | null;
  loading: boolean;
}

/**
 * Why each of one meeting's gaps was raised, and the meeting's name.
 *
 * The same shape as `useGapReport`, for the same reasons: every piece of state
 * carries its meeting, a later request wins over an earlier one, and a failed
 * refresh keeps the last good answer. Kept apart from the report because a
 * screen that cannot explain a gap should still list it.
 */
export function useGapExplanations(meetingId: string) {
  const [state, setState] = useState<State>({
    meetingId,
    explanations: null,
    error: null,
    loading: true,
  });
  const latest = useRef(0);

  const reload = useCallback(async () => {
    const ticket = latest.current + 1;
    latest.current = ticket;
    setState((previous) =>
      previous.meetingId === meetingId
        ? { ...previous, loading: true }
        : { meetingId, explanations: null, error: null, loading: true },
    );
    try {
      const explanations = await getExplanations(meetingId);
      if (ticket === latest.current) {
        setState({ meetingId, explanations, error: null, loading: false });
      }
    } catch (cause) {
      const error = cause instanceof Error ? cause : new Error(String(cause));
      if (ticket === latest.current) {
        setState((previous) => ({
          meetingId,
          explanations: previous.meetingId === meetingId ? previous.explanations : null,
          error,
          loading: false,
        }));
      }
    }
  }, [meetingId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (state.meetingId !== meetingId) {
    return { explanations: null, loading: true, error: null, reload };
  }
  return {
    explanations: state.explanations,
    loading: state.loading,
    error: state.error,
    reload,
  };
}
