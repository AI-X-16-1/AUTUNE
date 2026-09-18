"use client";

import { useEffect, useState } from "react";

import { getTranscript } from "../api";
import type { Utterance } from "../types";

export type TranscriptState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; utterances: Utterance[] };

/**
 * A meeting's stored transcript.
 *
 * Three states and no fourth. An empty transcript is `ready` with no rows, not
 * an error and not a spinner that never stops: the utterances are written in one
 * transaction when the task finishes, so "none yet" is what a meeting still
 * being processed honestly looks like. The caller decides how to say that,
 * because only the caller knows the meeting's status.
 *
 * Fetched once per `meetingId`. It does not poll — a stored transcript does not
 * change while somebody reads it, and the live view S13 wants is a different
 * channel that does not exist yet (see `api.getTranscript`). A polling loop here
 * would be a live view that is wrong about how live it is.
 *
 * The request is abandoned if the id changes before it lands, so a fast
 * navigation cannot leave the previous meeting's transcript on screen under the
 * new meeting's heading.
 */
export function useTranscript(meetingId: string): TranscriptState {
  const [state, setState] = useState<TranscriptState>({ status: "loading" });

  useEffect(() => {
    let current = true;
    setState({ status: "loading" });

    getTranscript(meetingId)
      .then((utterances) => {
        if (current) setState({ status: "ready", utterances });
      })
      .catch((error: unknown) => {
        // The message, never the body: an API error can quote what it refused.
        const message = error instanceof Error ? error.message : "전사를 불러오지 못했습니다";
        if (current) setState({ status: "error", message });
      });

    return () => {
      current = false;
    };
  }, [meetingId]);

  return state;
}
