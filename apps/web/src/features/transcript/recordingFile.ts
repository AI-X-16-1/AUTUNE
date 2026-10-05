/**
 * A recording as a file on the person's own device -- the one copy of audio
 * this feature may leave outside the tab (privacy.md section 1).
 *
 * Only for a live recording whose upload failed **and that the server does
 * not have**, and only on a press: closing the tab would otherwise lose the
 * meeting. Nothing calls this on its own, and once the server has the
 * recording the tab drops it, so "원본 처리 후 삭제" still holds for every
 * recording the server received.
 */
import { ApiError } from "@/shared/api/client";

import { getMeeting } from "./api";
import type { LivePhase } from "./hooks/useLiveSession";
import type { MeetingStatus } from "./types";

/** What S03 accepts, and what a saved live recording (`.webm`) must pass. */
export const ACCEPTED_EXTENSIONS = [".mp3", ".wav", ".m4a", ".webm"];

export function acceptsRecording(name: string): boolean {
  const dot = name.lastIndexOf(".");
  const ext = dot === -1 ? "" : name.slice(dot).toLowerCase();
  return ACCEPTED_EXTENSIONS.includes(ext);
}

/**
 * How long the object URL outlives the click. Revoked in the same tick, some
 * browsers never start a large download, and here a download that does not
 * start is a meeting lost. A minute costs one blob URL held in memory.
 */
const REVOKE_AFTER_MS = 60_000;

/**
 * Hand the recording to the browser's download. Named after the meeting id,
 * not its title: a title is the team's text and has no business in a file
 * name the operating system will index.
 */
export function saveRecordingFile(recording: Blob, meetingId: string): void {
  const url = URL.createObjectURL(recording);
  const link = document.createElement("a");
  link.href = url;
  link.download = `autune-녹음-${meetingId}.webm`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER_MS);
}

/** The recording the tab may hand over: only after a failed upload. */
export function recordingToSave(
  phase: LivePhase,
  recording: Blob | null,
): Blob | null {
  return phase === "upload_failed" ? recording : null;
}

/** Statuses a meeting reaches only once the server took its recording. */
const RECEIVED: ReadonlySet<MeetingStatus> = new Set([
  "analyzing",
  "awaiting_confirmation",
  "complete",
  "delivered",
]);

/**
 * Answers after which the bytes may or may not have arrived, so the meeting's
 * status decides: a gateway's (something in front of the API answered), and
 * 409 -- `start_transcription` refuses with it both a meeting already past
 * `recording` (received) and one whose live socket still holds its claim
 * (received nothing), under the same code.
 */
const ASK_THE_MEETING = new Set([409, 502, 503, 504]);

/**
 * Whether a failed upload is one the server in fact received. When unsure,
 * no: keeping a copy too long costs a file; dropping the only one costs the
 * meeting.
 *
 * - **409, a gateway's answer, or no answer** -- the meeting's status
 *   decides. Past `recording` means the server took the recording; still
 *   `recording` means it did not (a 409 for a live session not yet released,
 *   after `stop()` gave up waiting for `ended`). If even the meeting cannot
 *   be read, the tab cannot confirm the server has it and keeps the copy.
 * - **Any other answer from the API** -- it refused or lost the file.
 *   `enqueue_failed` (500) is the trap: the server accepted the bytes, then
 *   deleted them and failed the meeting, so the tab's copy is the only one.
 */
export async function serverHasRecording(
  meetingId: string,
  error: unknown,
): Promise<boolean> {
  if (error instanceof ApiError && !ASK_THE_MEETING.has(error.status)) {
    return false;
  }
  try {
    const meeting = await getMeeting(meetingId);
    return RECEIVED.has(meeting.status);
  } catch {
    return false;
  }
}
