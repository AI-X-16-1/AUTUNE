"use client";

import { useSpeakers } from "../hooks/useSpeakers";
import { useTranscript } from "../hooks/useTranscript";
import type { UtteranceKind } from "../types";
import { TranscriptRow } from "./TranscriptRow";
import { UnidentifiedSpeaker } from "./UnidentifiedSpeaker";

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
 *
 * **This is the only screen with an identification prompt.** No `Participant`
 * row exists for a meeting until `persist_transcript` writes them (via
 * `_participants_for`), in the same transaction as the utterances — so a
 * meeting still recording or still being processed has none, and `GET
 * /speakers` returns `[]` for it, not entries with `candidate: null`.
 * `LiveTranscript` carries no prompt at all for exactly that reason (see its
 * own docstring). By the time a transcript is stored, the participants exist
 * and the worker has written their observation vectors, so this is also the
 * one screen where a candidate can appear.
 *
 * **Which of the branches below show it.** Only the last one, past
 * "utterances exist". The loading and empty-transcript branches deliberately
 * have none — same reason as above, there is nothing to identify yet. The
 * error branch is different: it hides a speaker list that may have loaded
 * successfully, on account of the *transcript* fetch alone failing. That is
 * a real gap, not a deliberate one, and it is not fixed here.
 */
export function StoredTranscript({
  meetingId,
  teamId,
  kinds = {},
}: {
  meetingId: string;
  teamId: string | null;
  /** Classification per utterance id, if the page has already fetched them. */
  kinds?: Record<string, UtteranceKind>;
}) {
  const state = useTranscript(meetingId);
  const { speakers, members, assign, error: assignError, pending } = useSpeakers(
    meetingId,
    teamId,
  );
  const unidentified = speakers.filter((entry) => entry.user_id === null);

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
      {unidentified.map((entry) => (
        <UnidentifiedSpeaker
          key={entry.speaker_label}
          speaker={entry.speaker_label}
          candidate={entry.candidate}
          members={members}
          pending={pending}
          onAssign={(userId) => void assign(entry.speaker_label, userId)}
        />
      ))}

      {assignError && (
        <p
          role="alert"
          style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-attention)" }}
        >
          {assignError}
        </p>
      )}

      {state.utterances.map((utterance) => (
        <TranscriptRow
          key={utterance.id}
          row={{ utterance, kind: kinds[utterance.id] }}
        />
      ))}
    </section>
  );
}
