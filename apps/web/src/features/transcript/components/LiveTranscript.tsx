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
}) {
  const unidentified = countUnidentified(rows);
  const counts = countKinds(rows);

  return (
    <>
      <RecordingFrame state={state} />
      <div
        className="mx-auto flex gap-8"
        style={{ maxWidth: "var(--layout-canvasWide)", padding: "var(--space-page)" }}
      >
        <main className="min-w-0 flex-1">
          {[...unidentified.entries()].map(([speaker, count]) => (
            <UnidentifiedSpeaker
              key={speaker}
              speaker={speaker}
              utteranceCount={count}
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
            rows.map((row) => <TranscriptRow key={row.utterance.id} row={row} />)
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
 * How many utterances each unnamed voice has, in the order they first spoke.
 *
 * Counted rather than listed because the prompt asks about a voice, not about a
 * line: confirming one answers all of them.
 */
function countUnidentified(rows: LiveRow[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const { utterance } of rows) {
    if (utterance.speaker_id != null) continue;
    counts.set(utterance.speaker, (counts.get(utterance.speaker) ?? 0) + 1);
  }
  return counts;
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
