"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/shared/api/client";

import { getBrief } from "../api";
import type { BriefRead } from "../types";

/**
 * This meeting's pre-meeting brief, or `null` when it has none.
 *
 * A 404 is the ordinary answer, not an error: a finished meeting never had a
 * brief, and a scheduled one has none until ten minutes before it starts. Only
 * a failure that is not "there is no brief" reaches `error`.
 */
export function useBrief(meetingId: string) {
  const [brief, setBrief] = useState<BriefRead | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getBrief(meetingId)
      .then((found) => {
        if (cancelled) return;
        setBrief(found);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setBrief(null);
        if (cause instanceof ApiError && cause.status === 404) {
          setError(null);
        } else {
          setError(cause instanceof Error ? cause : new Error(String(cause)));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId]);

  return { brief, loading, error };
}
