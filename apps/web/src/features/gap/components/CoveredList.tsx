import { MaskedText, StatusDot } from "@/shared/ui";

import { COVERAGE_LABELS } from "../types";
import type { CoveredExplanation, GapExplanations, TemplateChecklistItem } from "../types";
import { EvidenceQuote } from "./GapList";

/**
 * The 충족 tab: the checklist items the meeting did cover.
 *
 * **No card, but a reason.** A covered item raises no gap, so it has no score
 * and no question — `gap_gaps` holds only `partial` and `missing`, and the
 * server reads the absence of a row back as `covered` (see `TemplateRail`).
 * What it can back is the topic that settled it and that topic's first lines,
 * which `/explanations` finds again over the stored graph (`covered`). When it
 * cannot — the template or the threshold moved since the meeting was analysed
 * — the row says so rather than guessing.
 *
 * In the order the template lists them, the order the rail reads in, so an
 * item is found in the same place on both sides. Not grouped by `category`:
 * that is a key (`measurement`), not copy a reader sees.
 */
export function CoveredList({
  items,
  explanations,
  meetingId,
}: {
  items: readonly TemplateChecklistItem[];
  explanations?: GapExplanations | null;
  meetingId?: string;
}) {
  if (items.length === 0) {
    return (
      <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
        충족으로 판정된 템플릿 항목이 없습니다.
      </p>
    );
  }

  const reasons = new Map((explanations?.covered ?? []).map((c) => [c.item_key, c]));

  return (
    <div className="flex flex-col" style={{ gap: "var(--space-16)" }}>
      <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
        표시 {items.length}건 · 충족한 항목은 갭을 만들지 않아 점수와 질문이 없습니다.
      </p>
      <ul className="border-t border-[var(--color-hairline)]">
        {items.map((item) => (
          <li
            key={item.key}
            className="flex flex-col border-b border-[var(--color-hairline)]"
            style={{ gap: "var(--space-8)", padding: "10px 0" }}
          >
            <div
              className="grid items-center"
              style={{ gridTemplateColumns: "auto 1fr auto", gap: "var(--space-8)" }}
            >
              <StatusDot variant="confirmed" />
              <span
                className="min-w-0 truncate text-[var(--color-ink-strong)]"
                style={{
                  fontSize: "var(--text-rowLabel)",
                  fontWeight: "var(--text-rowLabel-weight)",
                }}
              >
                {item.item}
              </span>
              <span
                className="text-[var(--color-ink-muted)]"
                style={{ fontSize: "var(--text-metaSmall)" }}
              >
                {COVERAGE_LABELS.covered}
              </span>
            </div>
            {explanations ? (
              <CoveredReason
                reason={reasons.get(item.key) ?? null}
                threshold={explanations.partial_centrality}
                meetingId={meetingId}
              />
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Why the item was read as covered, in the terms `detect.classify` used. */
function CoveredReason({
  reason,
  threshold,
  meetingId,
}: {
  reason: CoveredExplanation | null;
  threshold: number;
  meetingId?: string;
}) {
  if (!reason?.topic_label) {
    return (
      <p
        className="pl-5 text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        판정 근거를 다시 찾지 못했습니다. 회의를 분석한 뒤 템플릿이나 기준이 바뀌었을 수
        있습니다.
      </p>
    );
  }

  return (
    <div className="flex flex-col pl-5" style={{ gap: "var(--space-4)" }}>
      <p
        className="text-[var(--color-ink-body)]"
        style={{ fontSize: "var(--text-body)", lineHeight: "var(--text-body-leading)" }}
      >
        &quot;<MaskedText>{reason.topic_label}</MaskedText>&quot; 토픽으로 회의에서 충분히
        다뤄져(중심도 {fixed(reason.topic_centrality)}, 기준 {fixed(threshold)} 이상) 충족으로
        판정했습니다.
      </p>
      {reason.evidence.length > 0 ? (
        <ul className="flex flex-col" style={{ gap: "var(--space-4)" }}>
          {reason.evidence.map((quote) => (
            <li key={quote.utterance_id}>
              <EvidenceQuote quote={quote} meetingId={meetingId} />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function fixed(value: number | null): string {
  return value === null ? "–" : value.toFixed(2);
}
