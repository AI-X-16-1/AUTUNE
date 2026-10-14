/**
 * An assignee that is a diarization label, not a name, as a person reads it
 * (#1183): a bare `SPEAKER_01` told them nothing about what to do,
 * "SPEAKER_01(이름 미지정)" says the line is a voice nobody named yet.
 *
 * The label is kept as it is and never turned into a number: the transcript
 * numbers voices by first-spoken order (module A's `rename_speakers`), which is
 * not the index in the label, so a "화자 N" made up from `SPEAKER_01` could
 * point at a different voice than the transcript shows. A label that already
 * reads "화자 N" gets only the note. Anything else is a name somebody wrote and
 * is shown as it is.
 */
export function speakerLabelShown(label: string): string {
  if (/^SPEAKER_\d+$/i.test(label.trim())) return `${label.trim()}(이름 미지정)`;
  if (/^화자\s*\d+$/.test(label.trim())) return `${label.trim()}(이름 미지정)`;
  return label;
}

/** Whether `label` is a voice's label rather than a person's name. */
export function isSpeakerLabel(label: string): boolean {
  return speakerLabelShown(label) !== label;
}
