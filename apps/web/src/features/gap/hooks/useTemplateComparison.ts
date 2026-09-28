"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getTemplateComparison } from "../api";
import type { TemplateComparison } from "../types";

/** What the last settled request for one meeting left behind. */
interface State {
  meetingId: string;
  comparison: TemplateComparison | null;
  error: Error | null;
  loading: boolean;
}

/**
 * The template-comparison rail for one meeting.
 *
 * A third read beside `useGapReport` and `useTopicGraph`, and a third hook for
 * the same reason the screen keeps their states apart: the rail is a separate
 * endpoint, so a rail that is still arriving and a rail the meeting genuinely
 * has nothing for are different sentences, and only a per-section state can
 * tell them apart.
 *
 * The staleness guard is the one the other two carry — a response lands only
 * if no later request has started, for this meeting or another. The endpoint is
 * polled while the pipeline runs, so two requests in flight across a navigation
 * is the ordinary case rather than the edge one.
 */
export function useTemplateComparison(meetingId: string) {
  const [state, setState] = useState<State>({
    meetingId,
    comparison: null,
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
        : { meetingId, comparison: null, error: null, loading: true },
    );
    try {
      const comparison = await getTemplateComparison(meetingId);
      if (ticket === latest.current) {
        setState({ meetingId, comparison, error: null, loading: false });
      }
    } catch (cause) {
      const error = cause instanceof Error ? cause : new Error(String(cause));
      if (ticket === latest.current) {
        setState((previous) => ({
          meetingId,
          // A failed refresh leaves this meeting's rail where it was; a 404 on
          // the meeting just opened has nothing to leave.
          comparison: previous.meetingId === meetingId ? previous.comparison : null,
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
    return { comparison: null, loading: true, error: null, reload };
  }
  return {
    comparison: state.comparison,
    loading: state.loading,
    error: state.error,
    reload,
  };
}
