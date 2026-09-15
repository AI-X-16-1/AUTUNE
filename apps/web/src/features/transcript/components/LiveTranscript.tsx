"use client";

import { RecordingFrame } from "@/shared/ui";

import type { LiveRow, RecordingState, UtteranceKind } from "../types";
import { LiveRail } from "./LiveRail";
import { TranscriptRow } from "./TranscriptRow";
import { UnidentifiedSpeaker } from "./UnidentifiedSpeaker";

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
 * Unidentified speakers are collected into one prompt above the transcript
 * rather than repeated on every row of theirs. Answering it once answers every
 * row, which is also what the S16 DM does.
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
  onAssignSpeaker,
  onEnterSpeakerName,
  onSendConfirmation,
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
  onAssignSpeaker?: (speaker: string) => void;
  onEnterSpeakerName?: (speaker: string) => void;
  onSendConfirmation?: (speaker: string) => void;
  /** Whether module B has reported on this meeting.
   *
   * Not derived from the rows: a meeting B analysed and found nothing in looks
   * exactly like one B has not looked at yet, and only the caller knows which.
   * False keeps the rail's tally off the screen instead of printing zeroes for
   * every kind. */
  classified?: boolean;
}) {
  const unidentified = unidentifiedVoices(rows);
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
          {unidentified.map((speaker) => (
            <UnidentifiedSpeaker
              key={speaker}
              speaker={speaker}
              onAssign={() => onAssignSpeaker?.(speaker)}
              onEnterName={() => onEnterSpeakerName?.(speaker)}
              onSendConfirmation={() => onSendConfirmation?.(speaker)}
            />
          ))}

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

/**
 * Each unnamed voice once, in the order it first spoke.
 *
 * A list, not a tally. Counting how much each voice said is a per-person speech
 * volume, and a speaker number is not anonymity when everyone was in the room —
 * `privacy.md` section 3 forbids exactly this shape. Listing is also all the
 * prompt needs: it asks who a voice belongs to, and confirming one answers
 * every line that voice spoke.
 */
function unidentifiedVoices(rows: LiveRow[]): string[] {
  const seen: string[] = [];
  for (const { utterance } of rows) {
    if (utterance.speaker_id != null) continue;
    if (!seen.includes(utterance.speaker)) seen.push(utterance.speaker);
  }
  return seen;
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
