"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getReport } from "../api";
import type { GapReport } from "../types";

/** What the last settled request for one meeting left behind. */
interface State {
  meetingId: string;
  report: GapReport | null;
  error: Error | null;
  loading: boolean;
}

/**
 * One meeting's gap report, and whether it is still being built.
 *
 * `analysing` is not a third state the server reports — it cannot be. A report
 * with no topics is what both a meeting the pipeline has not reached and a
 * meeting whose transcript produced nothing look like, and the endpoint
 * deliberately answers 200 with empty lists for either. The screen tells the
 * user which one it is by what it says, not by pretending to know.
 *
 * **Every piece of state carries the meeting it belongs to.** A report reached
 * this hook for one `meetingId`, and a reader who has moved to another meeting
 * must never see it: the endpoint is polled while the pipeline runs, so two
 * requests are in flight across a navigation as a matter of course, and the
 * first one to answer is not the one the screen asked for last.
 */
export function useGapReport(meetingId: string) {
  const [state, setState] = useState<State>({
    meetingId,
    report: null,
    error: null,
    loading: true,
  });
  // A response lands only if no later request has started, for this meeting or
  // another. Without it a poll that overtakes its predecessor loses to the
  // slower, older answer.
  const latest = useRef(0);

  const reload = useCallback(async () => {
    const ticket = latest.current + 1;
    latest.current = ticket;
    setState((previous) =>
      previous.meetingId === meetingId
        ? { ...previous, loading: true }
        : { meetingId, report: null, error: null, loading: true },
    );
    try {
      const report = await getReport(meetingId);
      if (ticket === latest.current) setState({ meetingId, report, error: null, loading: false });
    } catch (cause) {
      const error = cause instanceof Error ? cause : new Error(String(cause));
      if (ticket === latest.current) {
        setState((previous) => ({
          meetingId,
          // A refresh that failed leaves this meeting's report where it was —
          // a 500 on one poll is not a reason to blank a report the reader is
          // reading. It can only ever leave this meeting's report there, so a
          // 404 on the meeting just opened shows an error and nothing else.
          report: previous.meetingId === meetingId ? previous.report : null,
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
  // meetingId still holds the previous one's state. It reads as loading, never
  // as this meeting's answer.
  if (state.meetingId !== meetingId) return { report: null, loading: true, error: null, reload };
  return { report: state.report, loading: state.loading, error: state.error, reload };
}
