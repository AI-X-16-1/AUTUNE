"use client";

import type { ReactNode } from "react";

import { RecordingFrame, StatusDot } from "@/shared/ui";

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
 *
 * **The status bar under the transcript says only what this page knows.**
 * S13 draws model names there too; they are the server's configuration, which
 * the browser cannot see, so printing them would be a claim the page cannot
 * check. What it can: whether the microphone track is live and whether the
 * browser applied noise suppression to it (`MediaStreamTrack.getSettings`),
 * and that PII masking is on, which is not a setting -- the live path masks
 * every line before it is sent (`autune_audio.live`).
 */
export type MicrophoneStatus = {
  /** The audio track exists and its `readyState` is `live`. */
  live: boolean;
  /** The browser reports `noiseSuppression: true` for the track. */
  noiseSuppression: boolean;
};

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
  microphone,
  onResearch,
  research,
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
  /** Undefined when the caller has no microphone to report on. */
  microphone?: MicrophoneStatus;
  /** Ask the agent to look up the row at this index (the 조사 button). */
  onResearch?: (index: number) => void;
  /** The 회의 중 조사 panel, drawn in the right rail under the controls. */
  research?: ReactNode;
}) {
  const counts = classified ? countKinds(rows) : undefined;

  return (
    <>
      <RecordingFrame state={state} />
      {/* S13's body: the transcript across the panel, and a 372px paper rail
          beside it behind a hairline. Full width — the screen has no sidebar,
          and a meeting in progress is the only thing on it. */}
      <div
        className="grid min-h-0 flex-1"
        style={{ gridTemplateColumns: "minmax(0, 1fr) 372px" }}
      >
        <div className="flex min-w-0 flex-col">
          <main className="min-w-0 flex-1" style={{ padding: "var(--space-12) var(--space-24)" }}>
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
              rows.map((row, i) => (
                <TranscriptRow
                  key={row.utterance.id}
                  row={row}
                  onResearch={onResearch ? () => onResearch(i) : undefined}
                />
              ))
            )}
          </main>
          <StatusBar microphone={microphone} />
        </div>

        <div className="overflow-y-auto border-l border-[var(--color-hairline)] bg-[var(--color-surface-paper)]">
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
          {research}
        </div>
      </div>
    </>
  );
}

/** S13's footer: the input and the masking, as far as this page can tell. */
function StatusBar({ microphone }: { microphone?: MicrophoneStatus }) {
  return (
    <footer
      className="flex flex-none items-center"
      style={{
        position: "sticky",
        bottom: 0,
        height: 52,
        gap: "var(--space-row)",
        padding: "0 var(--space-24)",
        borderTop: "1px solid var(--color-hairline)",
        background: "var(--color-surface-panel)",
        fontSize: "var(--text-meta)",
        fontWeight: "var(--text-meta-weight)",
        color: "var(--color-ink-muted)",
      }}
    >
      {microphone ? (
        <span className="flex items-center" style={{ gap: 7 }}>
          <StatusDot variant={microphone.live ? "confirmed" : "attention"} />
          {microphone.live ? "마이크 정상" : "마이크 끊김"}
        </span>
      ) : null}
      {microphone?.noiseSuppression ? <span>노이즈 제거 켬</span> : null}
      <span>PII 마스킹 켬</span>
    </footer>
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
