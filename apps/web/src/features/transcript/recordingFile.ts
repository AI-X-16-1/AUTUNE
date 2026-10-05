/**
 * A recording as a file on the person's own device -- the one copy of audio
 * this feature may leave outside the tab (privacy.md section 1).
 *
 * Only for a live recording whose upload failed, and only on a press: the
 * server never had it, and closing the tab would otherwise lose the meeting.
 * Nothing calls this on its own, and nothing keeps a copy after a successful
 * upload, so "원본 처리 후 삭제" still holds for every recording the server
 * received.
 */

/** What S03 accepts, and what a saved live recording (`.webm`) must pass. */
export const ACCEPTED_EXTENSIONS = [".mp3", ".wav", ".m4a", ".webm"];

export function acceptsRecording(name: string): boolean {
  const dot = name.lastIndexOf(".");
  const ext = dot === -1 ? "" : name.slice(dot).toLowerCase();
  return ACCEPTED_EXTENSIONS.includes(ext);
}

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
  URL.revokeObjectURL(url);
}
