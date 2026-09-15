"use client";

import { useEffect, useState } from "react";

import { getDecisionThread } from "../api";
import type { DecisionLineageRead } from "../types";

/**
 * One thread's full timeline, oldest version first (S22 right pane).
 *
 * `threadId` is nullable so the panel can render with nothing selected —
 * the topic list on the left is the thing that has data on first paint.
 */
export function useDecisionLineage(threadId: string | null) {
  const [lineage, setLineage] = useState<DecisionLineageRead | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (threadId === null) {
      setLineage(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getDecisionThread(threadId)
      .then((result) => {
        if (!cancelled) {
          setLineage(result);
          setError(null);
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause : new Error(String(cause)));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [threadId]);

  return { lineage, loading, error };
}
