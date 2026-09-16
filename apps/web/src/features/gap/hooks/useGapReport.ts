"use client";

import { useCallback, useEffect, useState } from "react";

import { getReport } from "../api";
import type { GapReport } from "../types";

/**
 * One meeting's gap report, and whether it is still being built.
 *
 * `analysing` is not a third state the server reports — it cannot be. A report
 * with no topics is what both a meeting the pipeline has not reached and a
 * meeting whose transcript produced nothing look like, and the endpoint
 * deliberately answers 200 with empty lists for either. The screen tells the
 * user which one it is by what it says, not by pretending to know.
 */
export function useGapReport(meetingId: string) {
  const [report, setReport] = useState<GapReport | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setReport(await getReport(meetingId));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setLoading(false);
    }
  }, [meetingId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { report, loading, error, reload };
}
