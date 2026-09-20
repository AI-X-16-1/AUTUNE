"use client";

import { useEffect, useState } from "react";

import { DEMO_PLANNED_SECONDS, DEMO_ROWS } from "../fixtures/live-demo";
import type { LiveRow, RecordingState } from "../types";
import { LiveTranscript } from "./LiveTranscript";

/** How many recent input levels the waveform keeps. */
const LEVEL_WINDOW = 48;

/**
 * S13 with a clock behind it: the container the route mounts.
 *
 * `LiveTranscript` is deliberately stateless — it draws whatever it is handed.
 * This component is the part that changes over time: the elapsed timer, the
 * waveform levels, pause and stop, and the rows as they arrive.
 *
 * Rows arrive from the demo meeting for now, each on its own `start`. When the
 * microphone path exists, the fixture becomes a socket and nothing below the
 * rows changes — that is why the clock and the feed are already separate.
 *
 * Speaker actions log for the moment. Assigning a speaker writes `speaker_id`
 * on a shared entity, which only module A does and only through `/api/audio`;
 * the endpoint is not there yet.
 */
export function LiveMeetingScreen({ meetingId }: { meetingId: string }) {
  const [state, setState] = useState<RecordingState>("recording");
  const [elapsed, setElapsed] = useState(0);
  const [levels, setLevels] = useState<number[]>(() =>
    Array.from({ length: LEVEL_WINDOW }, () => 0),
  );

  useEffect(() => {
    if (state !== "recording") return;
    const timer = setInterval(() => {
      setElapsed((s) => s + 1);
      setLevels((prev) => [...prev.slice(1), nextLevel(prev.at(-1) ?? 0)]);
    }, 1000);
    return () => clearInterval(timer);
  }, [state]);

  const rows: LiveRow[] = DEMO_ROWS.filter((r) => r.utterance.start <= elapsed);

  return (
    <LiveTranscript
      state={state}
      rows={rows}
      elapsedSeconds={elapsed}
      plannedSeconds={DEMO_PLANNED_SECONDS}
      levels={levels}
      onPause={() => setState("paused")}
      onResume={() => setState("recording")}
      onStop={() => setState("ended")}
      onAssignSpeaker={(speaker) =>
        console.log("assign speaker", { meetingId, speaker })
      }
      onEnterSpeakerName={(speaker) =>
        console.log("enter speaker name", { meetingId, speaker })
      }
      onSendConfirmation={(speaker) =>
        console.log("send confirmation DM", { meetingId, speaker })
      }
    />
  );
}

/** A level near the last one, so the waveform moves like speech, not noise. */
function nextLevel(last: number): number {
  const drift = (Math.random() - 0.5) * 0.5;
  return Math.min(1, Math.max(0.05, last + drift));
}
