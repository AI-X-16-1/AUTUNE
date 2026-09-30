"use client";

import { useEffect, useRef } from "react";

/** How often a meeting still being analysed is read again. */
export const POLL_MS = 5_000;

/**
 * How long a screen keeps asking before it stops.
 *
 * Transcription alone took about thirteen minutes on the measured recording
 * (#389), and the topic graph comes after it. Past this, a meeting that still
 * has no topics is far more likely one whose transcript produced none than one
 * still running — and the rail already says "아직 대조하지 않았습니다" for either,
 * so stopping costs the reader nothing but an automatic refresh.
 */
export const POLL_LIMIT_MS = 20 * 60_000;

/**
 * Read the report again while the pipeline has not reached this meeting.
 *
 * `analysed` is `TemplateComparison.analysed` — whether the meeting has a topic
 * graph — and `null` while the rail has not loaded. It is the only signal module
 * C has: the meeting's own processing status is module A's, and this feature
 * does not call another module's API.
 *
 * **One more read after `analysed` turns true.** The pipeline writes the topic
 * graph and then the gaps, back to back, so the poll that first sees topics can
 * land between the two and show a report with no gaps. The follow-up read closes
 * that window. A meeting that was already analysed when the screen opened gets
 * no follow-up, because there was nothing in flight.
 *
 * Skipped while the tab is hidden: a report nobody is looking at does not need
 * to be current until somebody looks, and the next visible tick catches up.
 */
export function usePollUntilAnalysed(analysed: boolean | null, reload: () => void): void {
  // The latest `reload`, so a new function identity each render does not
  // restart the interval.
  const latestReload = useRef(reload);
  useEffect(() => {
    latestReload.current = reload;
  }, [reload]);

  const wasWaiting = useRef(false);

  useEffect(() => {
    if (analysed === null) return undefined;

    if (analysed) {
      if (!wasWaiting.current) return undefined;
      wasWaiting.current = false;
      const followUp = setTimeout(() => latestReload.current(), POLL_MS);
      return () => clearTimeout(followUp);
    }

    wasWaiting.current = true;
    const startedAt = Date.now();
    const timer = setInterval(() => {
      if (Date.now() - startedAt > POLL_LIMIT_MS) {
        clearInterval(timer);
        return;
      }
      if (document.visibilityState === "visible") latestReload.current();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [analysed]);
}
