/**
 * Elapsed time, written the same way everywhere on this screen.
 *
 * The row timecode and the rail timer read the same clock, so they print the
 * same shape: `07:42` under an hour, `01:15:30` over one. Two implementations
 * had it `75:30` in one place and `01:15:30` in the other on the same meeting.
 */
export function timecode(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = Math.floor(seconds % 60);
  const pad = (n: number) => String(n).padStart(2, "0");
  return hours > 0
    ? `${pad(hours)}:${pad(minutes)}:${pad(rest)}`
    : `${pad(minutes)}:${pad(rest)}`;
}

/**
 * When a meeting started, in the reader's own timezone. `2026. 3. 4. 14:30`.
 *
 * Written out by hand rather than through `Intl.DateTimeFormat` on purpose.
 * The API sends UTC and the browser shows local time, which is right; what is
 * not right is a string whose shape depends on the machine's locale, because
 * the server renders this component's first pass too and a locale the two
 * disagree about is a hydration mismatch. The digits come from the `Date`, the
 * punctuation from here.
 *
 * Null for a meeting with no start time — a recording uploaded after the fact
 * (`MeetingSummary.started_at`). The caller says what to print instead; this
 * does not invent a date, and an unparseable value is treated the same way
 * rather than rendered as `Invalid Date`.
 */
export function meetingDate(iso: string | null): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${at.getFullYear()}. ${at.getMonth() + 1}. ${at.getDate()}. ` +
    `${pad(at.getHours())}:${pad(at.getMinutes())}`
  );
}
