"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { createDecision, deleteDecision, getReview, reviewDecision } from "../api";
import type { DecisionStatus, MeetingReview, ReviewDecision } from "../types";

/** What the last settled request for one meeting left behind. */
interface State {
  meetingId: string;
  review: MeetingReview | null;
  error: Error | null;
  loading: boolean;
}

/**
 * One meeting's review (#246): the decisions a person confirms before anything
 * leaves Autune, and the weak assents waiting on their speakers.
 *
 * Keyed by meeting with a ticket per request, the same rule as
 * `useActionItems` and `useGapReport`: the screen stays mounted across meetings,
 * and an older answer must not land on a newer meeting.
 *
 * Every change comes back as the decision the server now holds, and replaces
 * the local one. The screen never guesses what "delete" did to a model's
 * decision — the server rejects it rather than deleting it, so a rerun cannot
 * propose it again — it re-reads.
 */
export function useDecisionReview(meetingId: string) {
  const [state, setState] = useState<State>({
    meetingId,
    review: null,
    error: null,
    loading: true,
  });
  const latest = useRef(0);

  const reload = useCallback(async () => {
    const ticket = latest.current + 1;
    latest.current = ticket;
    setState((previous) =>
      previous.meetingId === meetingId
        ? { ...previous, error: null, loading: true }
        : { meetingId, review: null, error: null, loading: true },
    );
    try {
      const review = await getReview(meetingId);
      if (ticket === latest.current) setState({ meetingId, review, error: null, loading: false });
    } catch (cause) {
      const error = cause instanceof Error ? cause : new Error(String(cause));
      if (ticket === latest.current) {
        setState((previous) => ({
          meetingId,
          review: previous.meetingId === meetingId ? previous.review : null,
          error,
          loading: false,
        }));
      }
    }
  }, [meetingId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** Put one decision the server returned in place of the local one. */
  const replace = useCallback(
    (decision: ReviewDecision, append = false) =>
      setState((previous) => {
        if (previous.meetingId !== meetingId || previous.review === null) return previous;
        const decisions = append
          ? [...previous.review.decisions, decision]
          : previous.review.decisions.map((d) => (d.id === decision.id ? decision : d));
        return {
          ...previous,
          review: {
            ...previous.review,
            decisions,
            // The server's own definition (`MeetingReview.pending_decisions`),
            // recomputed so the header moves with the click. If the server ever
            // counts differently, this is the line to delete rather than the
            // place to guess. Raised in review of #298.
            pending_decisions: decisions.filter((d) => d.status === "pending").length,
          },
        };
      }),
    [meetingId],
  );

  const setStatus = useCallback(
    async (id: string, status: DecisionStatus) => replace(await reviewDecision(id, { status })),
    [replace],
  );

  const reword = useCallback(
    async (id: string, statement: string) => replace(await reviewDecision(id, { statement })),
    [replace],
  );

  const add = useCallback(
    async (statement: string) => replace(await createDecision(meetingId, statement), true),
    [meetingId, replace],
  );

  const remove = useCallback(
    async (id: string) => {
      await deleteDecision(id);
      await reload();
    },
    [reload],
  );

  if (state.meetingId !== meetingId) {
    return { review: null, loading: true, error: null, reload, setStatus, reword, add, remove };
  }
  return { ...state, reload, setStatus, reword, add, remove };
}
