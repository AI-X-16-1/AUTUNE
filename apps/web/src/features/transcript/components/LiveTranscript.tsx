"use client";

import { RecordingFrame } from "@/shared/ui";

import type { LiveRow, RecordingState, UtteranceKind } from "../types";
import { LiveRail } from "./LiveRail";
import { TranscriptRow } from "./TranscriptRow";

/**
 * S13. The meeting as it is being transcribed.
 *
 * Light theme and the recording frame, always: recording is a state of the
 * meeting rather than a user preference, so the glow is identical in both
 * themes and the frame is the only thing on screen that says "this is being
 * recorded". `RecordingFrame` handles prefers-reduced-motion.
 *
 * **Rows appear as they are transcribed and never move afterwards.** A live
 * transcript that reflows when a later model pass revises an earlier line is
 * unreadable — somebody is reading it while it is written. Corrections land in
 * S15 after the meeting, where re-reading is the point.
 *
 * **No identification prompt during a recording, on purpose.** Putting a name
 * to a voice needs a `Participant` row, and none exists for a meeting until
 * `persist_transcript` writes them (via `_participants_for`) in the same
 * transaction as the utterances — a meeting that is still `recording` or
 * still `analyzing` has none at all, so `GET /speakers` for it returns `[]`,
 * not a list of unidentified labels. This screen used to derive a label list
 * straight from the rows' `speaker` strings and offer buttons that only
 * `console.log`ed; removing that was not a regression, because none of it
 * ever wrote an assignment. `StoredTranscript` is where a name gets attached,
 * once the meeting is processed and the participants — and, later, their
 * candidates — exist to attach one to.
 */
export function LiveTranscript({
  state,
  rows,
  elapsedSeconds,
  plannedSeconds,
  levels,
  onPause,
  onResume,
  onStop,
  classified = false,
}: {
  state: RecordingState;
  rows: LiveRow[];
  elapsedSeconds: number;
  plannedSeconds?: number;
  levels: number[];
  onPause?: () => void;
  onResume?: () => void;
  onStop?: () => void;
  /** Whether module B has reported on this meeting.
   *
   * Not derived from the rows: a meeting B analysed and found nothing in looks
   * exactly like one B has not looked at yet, and only the caller knows which.
   * False keeps the rail's tally off the screen instead of printing zeroes for
   * every kind. */
  classified?: boolean;
}) {
  const counts = classified ? countKinds(rows) : undefined;

  return (
    <>
      <RecordingFrame state={state} />
      <div
        className="mx-auto flex gap-8"
        style={{
          maxWidth: "var(--layout-canvasWide)",
          padding: "var(--space-page)",
        }}
      >
        <main className="min-w-0 flex-1">
          {rows.length === 0 ? (
            <p
              style={{
                color: "var(--color-ink-muted)",
                paddingBlock: "var(--space-page)",
              }}
            >
              {state === "recording"
                ? "듣고 있습니다. 말씀을 시작하시면 이곳에 전사됩니다."
                : "전사된 내용이 없습니다."}
            </p>
          ) : (
            rows.map((row) => (
              <TranscriptRow key={row.utterance.id} row={row} />
            ))
          )}
        </main>

        <LiveRail
          state={state}
          elapsedSeconds={elapsedSeconds}
          plannedSeconds={plannedSeconds}
          levels={levels}
          counts={counts}
          onPause={onPause}
          onResume={onResume}
          onStop={onStop}
        />
      </div>
    </>
  );
}

/** Per kind, per meeting. Never per person — `privacy.md` section 3. */
function countKinds(rows: LiveRow[]): Partial<Record<UtteranceKind, number>> {
  const counts: Partial<Record<UtteranceKind, number>> = {};
  for (const { kind } of rows) {
    if (!kind) continue;
    counts[kind] = (counts[kind] ?? 0) + 1;
  }
  return counts;
}
