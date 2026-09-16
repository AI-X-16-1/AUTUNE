"use client";

import { useCallback, useEffect, useState } from "react";

import { listDecisionThreads, type DecisionThreadFilter } from "../api";
import type { DecisionSummaryRead } from "../types";

/** A team's decision threads by current head, for the S22 topic list. */
export function useDecisionThreads(filter: DecisionThreadFilter) {
  const [threads, setThreads] = useState<DecisionSummaryRead[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  const key = JSON.stringify(filter);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setThreads(await listDecisionThreads(JSON.parse(key) as DecisionThreadFilter));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setLoading(false);
    }
  }, [key]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { threads, loading, error, reload };
}
