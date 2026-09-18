"use client";

import { useTranscript } from "../hooks/useTranscript";
import type { UtteranceKind } from "../types";
import { TranscriptRow } from "./TranscriptRow";

/**
 * A finished meeting's transcript, read back from what was stored.
 *
 * The S15 transcript tab rather than S13. `LiveTranscript` in this folder draws
 * a meeting as it is being recorded — a frame that glows, a level meter, rows
 * that must never move once written. None of that applies here: the meeting is
 * over, re-reading is the point, and there is no recording to show the state of.
 * Sharing `TranscriptRow` between the two is deliberate; drawing a line of
 * transcript two ways is how the same redaction comes to look like two things.
 *
 * **Empty is a state, not an error.** Utterances are written in one transaction
 * when the task finishes, so a meeting still being processed has none. Saying
 * "아직 없습니다" is honest; a spinner that never resolves is not.
 *
 * Classifications are not fetched. The tag on a row comes from module B and this
 * feature may not call another module's endpoints — `CLAUDE.md` in this folder,
 * and the boundary exists so B can change its shape without breaking A's screen.
 * A page that wants tags joins the two itself.
 */
export function StoredTranscript({
  meetingId,
  kinds = {},
}: {
  meetingId: string;
  /** Classification per utterance id, if the page has already fetched them. */
  kinds?: Record<string, UtteranceKind>;
}) {
  const state = useTranscript(meetingId);

  if (state.status === "loading") {
    return (
      <p className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
        전사를 불러오는 중입니다…
      </p>
    );
  }

  if (state.status === "error") {
    return (
      <p role="alert" style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-attention)" }}>
        {state.message}
      </p>
    );
  }

  if (state.utterances.length === 0) {
    return (
      <p className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
        아직 전사된 내용이 없습니다. 분석이 끝나면 여기에 표시됩니다.
      </p>
    );
  }

  return (
    <section aria-label="회의 전사">
      {state.utterances.map((utterance) => (
        <TranscriptRow
          key={utterance.id}
          row={{ utterance, kind: kinds[utterance.id] }}
        />
      ))}
    </section>
  );
}
