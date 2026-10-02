import { MaskedText } from "@/shared/ui";

import { timecode } from "../format";
import type { LiveRow } from "../types";
import { KindTag } from "./KindTag";

/**
 * One line of the live transcript: time code, speaker, body, and the tag module
 * B gave it.
 *
 * **An unidentified speaker is shown, not guessed.** Diarization separates
 * voices without naming them, so `speaker_id` is null until somebody confirms
 * who a voice belongs to. The row goes ochre — "needs a person" in the token
 * vocabulary — rather than quietly picking the most likely name. Guessing here
 * would put one person's commitments under another's name, and the reader has
 * no way to tell it happened.
 */

export function TranscriptRow({ row, name }: { row: LiveRow; name?: string | null }) {
  const { utterance, kind } = row;
  // `name` is who the label was confirmed as, when the caller knows. The live
  // screen never does -- no one is named during a recording -- and passes none.
  const unidentified = name == null && utterance.speaker_id == null;

  return (
    <article
      className="grid items-baseline gap-3"
      style={{
        gridTemplateColumns: "52px 92px 1fr auto",
        paddingBlock: "var(--space-12)",
        borderBottom: "1px solid var(--color-hairline)",
      }}
    >
      <time
        className="tabular-nums"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "var(--text-data)",
          color: "var(--color-ink-muted)",
        }}
      >
        {timecode(utterance.start)}
      </time>

      <span
        style={{
          fontSize: "var(--text-status)",
          fontWeight: "var(--text-status-weight)",
          color: unidentified
            ? "var(--color-signal-attention)"
            : "var(--color-ink-strong)",
        }}
      >
        {name ?? utterance.speaker}
      </span>

      {/* `data-utterance-id` lets S30 turn a text selection into offsets in this utterance. */}
      <p data-utterance-id={utterance.id} style={{ color: "var(--color-ink-body)" }}>
        <MaskedText>{utterance.text}</MaskedText>
      </p>

      {kind ? <KindTag kind={kind} /> : <span aria-hidden />}
    </article>
  );
}
