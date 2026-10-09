import { MaskedText, StatusDot } from "@/shared/ui";

import { timecode } from "../format";
import type { LiveRow } from "../types";
import { KindTag } from "./KindTag";

/**
 * One line of the transcript: a time column, then the speaker above what they
 * said, then the tag module B gave it, if any.
 *
 * S13's layout -- a 64px mono time column beside a stacked block -- rather than
 * one line of four columns: the body gets the full width, and a tag sits under
 * the sentence it describes instead of in a column most rows leave empty. The
 * S15 transcript tab (`StoredTranscript`) draws the same row.
 *
 * **An unidentified speaker is shown, not guessed.** Diarization separates
 * voices without naming them, so `speaker_id` is null until somebody confirms
 * who a voice belongs to. The row says so -- an ochre dot and an ochre
 * "미확인" beside the label, ochre meaning "needs a person" in the token
 * vocabulary -- rather than quietly picking the most likely name. Guessing
 * here would put one person's commitments under another's name, and the
 * reader has no way to tell it happened. The label itself stays in ink: it is
 * still the right name for the voice, only not yet a person.
 */

export function TranscriptRow({
  row,
  name,
  onResearch,
}: {
  row: LiveRow;
  name?: string | null;
  /** Live screen only: ask the agent to look this line up. */
  onResearch?: () => void;
}) {
  const { utterance, kind } = row;
  // `name` is who the label was confirmed as, when the caller knows. The live
  // screen never does -- no one is named during a recording -- and passes none.
  const unidentified = name == null && utterance.speaker_id == null;

  return (
    <article
      className="grid"
      style={{
        gridTemplateColumns: "64px minmax(0, 1fr)",
        gap: "var(--space-row)",
        paddingBlock: "var(--space-12)",
        borderBottom: "1px solid var(--color-hairline)",
      }}
    >
      <time
        className="tabular-nums"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "var(--text-data)",
          fontWeight: "var(--text-data-weight)",
          color: "var(--color-ink-muted)",
          paddingTop: 2,
        }}
      >
        {timecode(utterance.start)}
      </time>

      <div className="min-w-0">
        <div
          className="flex items-center"
          style={{
            gap: "var(--space-8)",
            fontSize: "var(--text-rowTitle)",
            fontWeight: "var(--text-rowTitle-weight)",
            color: "var(--color-ink-strong)",
          }}
        >
          {unidentified ? <StatusDot variant="attention" /> : null}
          <span>
            {name ?? utterance.speaker}
            {unidentified ? (
              <span
                style={{
                  fontWeight: "var(--text-meta-weight)",
                  color: "var(--color-signal-attention)",
                }}
              >
                {" "}
                미확인
              </span>
            ) : null}
          </span>
          {onResearch ? (
            <button
              type="button"
              onClick={onResearch}
              aria-label={`${timecode(utterance.start)} 줄 조사`}
              style={{
                marginLeft: "auto",
                fontSize: "var(--text-meta)",
                fontWeight: "var(--text-meta-weight)",
                color: "var(--color-ink-muted)",
              }}
            >
              조사
            </button>
          ) : null}
        </div>

        {/* `data-utterance-id` lets S30 turn a text selection into offsets in this utterance. */}
        <p
          data-utterance-id={utterance.id}
          style={{
            marginTop: 3,
            fontSize: "var(--text-body)",
            lineHeight: "var(--text-body-leading)",
            color: "var(--color-ink-body)",
          }}
        >
          <MaskedText>{utterance.text}</MaskedText>
        </p>

        {kind ? (
          <div style={{ marginTop: 7 }}>
            <KindTag kind={kind} />
          </div>
        ) : null}
      </div>
    </article>
  );
}
