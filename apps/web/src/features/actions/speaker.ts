/**
 * A diarization label as a person reads it: `SPEAKER_01` is "화자 2" -- the
 * same numbering module A's processing screen uses ("화자 1, 2 ..."). Any
 * other label, a typed name among them, is shown as it is.
 */
export function shownLabel(label: string | null | undefined): string | undefined {
  if (!label) return undefined;
  const match = /^SPEAKER_(\d+)$/.exec(label);
  return match ? `화자 ${Number(match[1]) + 1}` : label;
}
