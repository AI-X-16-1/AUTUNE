/**
 * An assignee that is a diarization label, not a name, as a person reads it
 * (#1183): `SPEAKER_01` told them nothing about what to do, "화자 2(이름 미지정)"
 * says the line is a voice nobody named yet.
 *
 * pyannote counts from zero, so `SPEAKER_01` is the second voice -- "화자 2",
 * the way the transcript numbers the same voice. A label that already reads
 * "화자 N" gets only the note. Anything else is a name somebody wrote and is
 * shown as it is.
 */
export function speakerLabelShown(label: string): string {
  const raw = /^SPEAKER_(\d+)$/i.exec(label.trim());
  if (raw) return `화자 ${Number(raw[1]) + 1}(이름 미지정)`;
  if (/^화자\s*\d+$/.test(label.trim())) return `${label.trim()}(이름 미지정)`;
  return label;
}

/** Whether `label` is a voice's label rather than a person's name. */
export function isSpeakerLabel(label: string): boolean {
  return speakerLabelShown(label) !== label;
}
