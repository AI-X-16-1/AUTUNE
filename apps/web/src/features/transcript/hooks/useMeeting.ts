"use client";

import { useEffect, useState } from "react";

import { getMeeting } from "../api";
import type { MeetingDetail, MeetingStatus } from "../types";

export type MeetingState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; meeting: MeetingDetail };

/** Statuses the pipeline is still moving through; everything else is at rest. */
const IN_FLIGHT: ReadonlySet<MeetingStatus> = new Set([
  "recording",
  "analyzing",
]);

const POLL_MS = 3000;

/**
 * A meeting's own row, followed until the pipeline is done with it.
 *
 * This is the one place in the feature that polls, and it polls the meeting,
 * not the transcript. `useTranscript` deliberately does not — a stored
 * transcript does not change while somebody reads it — and the reason a
 * screen kept showing "아직 없습니다" was that nothing told it when to look
 * again. The status is that signal: S12 draws stages from it, and S15 reads
 * the transcript once it says `complete`.
 *
 * Every three seconds while `analyzing`; once, otherwise. The interval is not
 * a progress feed — there is no per-stage progress on the backend and this
 * hook does not pretend there is (`ProcessingStages`). WebSocket progress is
 * what the spec asks for and does not exist yet.
 *
 * A failed poll keeps the last good meeting on screen rather than replacing it
 * with an error: a blip in the middle of a ten-minute transcription should not
 * turn the stages red. Only the first load reports an error.
 */
export function useMeeting(meetingId: string): MeetingState {
  const [state, setState] = useState<MeetingState>({ status: "loading" });

  useEffect(() => {
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setState({ status: "loading" });

    const poll = () => {
      getMeeting(meetingId)
        .then((meeting) => {
          if (!current) return;
          setState({ status: "ready", meeting });
          if (IN_FLIGHT.has(meeting.status)) timer = setTimeout(poll, POLL_MS);
        })
        .catch((error: unknown) => {
          if (!current) return;
          setState((previous) => {
            if (previous.status === "ready") {
              // Keep what we had; try again if it was still moving.
              if (IN_FLIGHT.has(previous.meeting.status))
                timer = setTimeout(poll, POLL_MS);
              return previous;
            }
            const message =
              error instanceof Error
                ? error.message
                : "회의 정보를 불러오지 못했습니다";
            return { status: "error", message };
          });
        });
    };

    poll();
    return () => {
      current = false;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [meetingId]);

  return state;
}
