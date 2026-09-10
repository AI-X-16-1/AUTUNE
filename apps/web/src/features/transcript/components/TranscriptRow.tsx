import { PiiToken } from "@/shared/ui";

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

// Masked spans arrive as shape-preserving runs: 010-****-5678, k***@example.com.
// Splitting on the mask characters is what makes a redaction distinguishable
// from a typo. Duplicated from features/actions for now; see issue #139, which
// asks for the one copy to move to shared/ui. PR #141 does that move -- when it
// lands, delete this and import MaskedText from @/shared/ui. Until then the two
// screens can drift into drawing a redaction differently, which is the whole
// reason the issue exists.
const MASKED = /(\S*\*{2,}\S*)/g;

function timecode(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60);
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

export function TranscriptRow({ row }: { row: LiveRow }) {
  const { utterance, kind } = row;
  const unidentified = utterance.speaker_id == null;

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
          color: unidentified ? "var(--color-signal-attention)" : "var(--color-ink-strong)",
        }}
      >
        {utterance.speaker}
      </span>

      <p style={{ color: "var(--color-ink-body)" }}>
        {utterance.text.split(MASKED).map((part, index) =>
          // The capturing split alternates plain / matched, so odd indices are
          // the masked runs. Keys are positional because a span can repeat.
          index % 2 === 1 ? <PiiToken key={index}>{part}</PiiToken> : part,
        )}
      </p>

      {kind ? <KindTag kind={kind} /> : <span aria-hidden />}
    </article>
  );
}
