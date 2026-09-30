"use client";

import { useEffect, useState } from "react";

import { getDecision } from "../api";
import type { SourceUtterance } from "../types";

/**
 * The words a decision was settled in, fetched when its row is on screen — one
 * request per row, since the list carries ids only.
 *
 * A decision a person added has no sources and nothing to fetch. A response only
 * lands if it is still the row on screen, the way `useSourceUtterances` does it.
 */
export function useDecisionSources(id: string, sourceCount: number) {
  const [state, setState] = useState<{
    id: string;
    sources: SourceUtterance[] | null;
    error: Error | null;
  }>({ id, sources: null, error: null });

  useEffect(() => {
    if (sourceCount === 0) return;
    let current = true;
    getDecision(id).then(
      (detail) => {
        if (current) setState({ id, sources: detail.sources, error: null });
      },
      (cause: unknown) => {
        if (current) {
          setState({ id, sources: null, error: cause instanceof Error ? cause : new Error(String(cause)) });
        }
      },
    );
    return () => {
      current = false;
    };
  }, [id, sourceCount]);

  if (sourceCount === 0) return { sources: [] as SourceUtterance[], loading: false, error: null };
  const mine = state.id === id;
  return {
    sources: mine ? state.sources : null,
    loading: !mine || (state.sources === null && !state.error),
    error: mine ? state.error : null,
  };
}
