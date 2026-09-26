"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getTopicGraph } from "../api";
import type { TopicGraph } from "../types";

/** What the last settled request for one meeting left behind. */
interface State {
  meetingId: string;
  graph: TopicGraph | null;
  error: Error | null;
  loading: boolean;
}

/**
 * One meeting's topic graph, and whether it is still being built.
 *
 * The same shape and the same race as `useGapReport`, against the other half of
 * the module's read API. Both are polled while the pipeline runs, so two
 * requests are in flight across a navigation as a matter of course and the
 * first to answer is not the one the screen asked for last — every piece of
 * state carries the meeting it belongs to, and a response lands only if no
 * later request has started.
 *
 * Kept separate from `useGapReport` rather than folded into it: a screen
 * showing only the gap list should not wait on the graph, and a graph that
 * fails is not a reason to blank the report beside it.
 */
export function useTopicGraph(meetingId: string) {
  const [state, setState] = useState<State>({
    meetingId,
    graph: null,
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
        : { meetingId, graph: null, error: null, loading: true },
    );
    try {
      const graph = await getTopicGraph(meetingId);
      if (ticket === latest.current) setState({ meetingId, graph, error: null, loading: false });
    } catch (cause) {
      const error = cause instanceof Error ? cause : new Error(String(cause));
      if (ticket === latest.current) {
        setState((previous) => ({
          meetingId,
          // A failed refresh leaves this meeting's graph where it was; a 404 on
          // the meeting just opened shows an error and nothing else.
          graph: previous.meetingId === meetingId ? previous.graph : null,
          error,
          loading: false,
        }));
      }
    }
  }, [meetingId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // The reset above runs in an effect, so the render that first sees a new
  // meetingId still holds the previous one's state. It reads as loading.
  if (state.meetingId !== meetingId) return { graph: null, loading: true, error: null, reload };
  return { graph: state.graph, loading: state.loading, error: state.error, reload };
}
