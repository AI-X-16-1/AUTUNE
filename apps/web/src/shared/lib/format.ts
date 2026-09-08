/**
 * Monospace is only for machine-produced values: time codes, scores, ids,
 * dates, percentages. These helpers produce those strings.
 */

/** 3725.4 -> "1:02:05" */
export function timecode(seconds: number): string {
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return `${h > 0 ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

/** 0.124 -> "12%" */
export function percent(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}
