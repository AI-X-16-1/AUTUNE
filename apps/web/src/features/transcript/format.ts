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
