/** Korea keeps no daylight saving and no team timezone exists yet — same as `dates.py`. */
const KST_DAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/**
 * The KST calendar day of a timestamp the server sends, as `YYYY-MM-DD`.
 *
 * Not `iso.slice(0, 10)`: that is the day in UTC, which in Korea is still
 * yesterday until 09:00, so a morning meeting read as the day before (#231).
 * KST, not the viewer's zone, so the day agrees with `linked_meeting_date`,
 * which the server already writes as a KST day.
 */
export function kstDay(iso: string): string {
  const parts = KST_DAY.formatToParts(new Date(iso));
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((p) => p.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}
