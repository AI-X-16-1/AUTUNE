"use client";

import { RecordingFrame } from "@/shared/ui";

import { useSpeakers } from "../hooks/useSpeakers";
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
 *
 * **Candidates never appear here.** `useSpeakers` calls `GET /speakers`, but
 * the observation vector a candidate is drawn from is written by the worker
 * after the upload finishes, so a meeting still being recorded has none — the
 * endpoint returns `candidate: null` for every entry. `StoredTranscript` is
 * the screen where a candidate exists and gets offered.
 */
export function LiveTranscript({
  meetingId,
  teamId,
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
  meetingId: string;
  teamId: string | null;
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
  const { speakers, members, assign } = useSpeakers(meetingId, teamId);
  const unidentified = speakers.filter((entry) => entry.user_id === null);
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
          {unidentified.map((entry) => (
            <UnidentifiedSpeaker
              key={entry.speaker_label}
              speaker={entry.speaker_label}
              candidate={entry.candidate}
              members={members}
              onAssign={(userId) => void assign(entry.speaker_label, userId)}
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

/** Per kind, per meeting. Never per person — `privacy.md` section 3. */
function countKinds(rows: LiveRow[]): Partial<Record<UtteranceKind, number>> {
  const counts: Partial<Record<UtteranceKind, number>> = {};
  for (const { kind } of rows) {
    if (!kind) continue;
    counts[kind] = (counts[kind] ?? 0) + 1;
  }
  return counts;
}
