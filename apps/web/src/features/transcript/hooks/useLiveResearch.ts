"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { detectLive, listLiveResearch, researchLive } from "../api";
import type { LiveResearchDocument, LiveRow } from "../types";

const EVERY_ROWS = 6;
const EVERY_MS = 45_000;
const MAX_ROWS = 12;
const CONTEXT = 4;
const POLL_MS = 5_000;

const line = (row: LiveRow) => ({ start: row.utterance.start, text: row.utterance.text });

/**
 * Live research for the live screen: relays new rows to the agent layer and
 * reads back what it found.
 *
 * **Only `{start, text}` leaves the page** -- the speaker label may be a name,
 * and the model never needs one. A window goes after six new rows, or after
 * 45 seconds with at least one; one request at a time. A 429 means the
 * meeting has its five automatic documents, and the hook stops sending.
 */
export function useLiveResearch(meetingId: string, rows: LiveRow[], active: boolean) {
  const [docs, setDocs] = useState<LiveResearchDocument[]>([]);
  const sent = useRef(0);
  const inFlight = useRef(false);
  const capped = useRef(false);
  const latest = useRef(rows);
  latest.current = rows;

  const refresh = useCallback(() => {
    listLiveResearch(meetingId)
      .then(setDocs)
      .catch(() => {
        // An extra beside the transcript; a failed read leaves the last list.
      });
  }, [meetingId]);

  const send = useCallback(() => {
    const all = latest.current;
    if (!active || capped.current || inFlight.current || all.length <= sent.current) return;
    const window = all.slice(Math.max(sent.current, all.length - MAX_ROWS)).map(line);
    sent.current = all.length;
    inFlight.current = true;
    detectLive(meetingId, window)
      .catch((err: { status?: number }) => {
        if (err?.status === 429) capped.current = true;
      })
      .finally(() => {
        inFlight.current = false;
      });
  }, [active, meetingId]);

  useEffect(() => {
    if (rows.length - sent.current >= EVERY_ROWS) send();
  }, [rows.length, send]);

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(send, EVERY_MS);
    return () => clearInterval(timer);
  }, [active, send]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // A document still running keeps the list polling after a pause or stop;
  // reading only while recording froze it at 조사 중 for good.
  const running = docs.some((doc) => doc.status === "running");
  useEffect(() => {
    if (!active && !running) return;
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [active, running, refresh]);

  const research = useCallback(
    async (index: number) => {
      const all = latest.current;
      const row = all[index];
      if (!row) return;
      await researchLive(
        meetingId,
        line(row),
        all.slice(Math.max(0, index - CONTEXT), index).map(line),
      ).catch(() => {
        // 409: already researched, or the meeting is full; the list says which.
      });
      refresh();
    },
    [meetingId, refresh],
  );

  return { docs, research };
}
