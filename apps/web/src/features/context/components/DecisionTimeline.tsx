import { Quote, StatusDot } from "@/shared/ui";

import type { ChangeType, DecisionVersionRead, NliLabel } from "../types";

const CHANGE_LABEL: Record<ChangeType, string> = {
  unchanged: "유지",
  modified: "수정",
  reversed: "번복",
  new: "신규",
};

const NLI_LABEL: Record<NliLabel, string> = {
  entailment: "일관",
  contradiction: "모순",
  neutral: "중립",
};

/**
 * A thread's versions as vertical nodes, oldest first (S22).
 *
 * Node style follows position, not `change_type` alone: the first version is
 * always a hollow ring ("원본") and the last is always ink ("현재"), even
 * when its own `change_type` is `unchanged` — the ring/ink pair marks where
 * in the timeline you are, not whether that particular version changed
 * anything. Everything between is ochre, with the reason block the spec
 * calls for: who was absent and what the NLI comparison found.
 */
export function DecisionTimeline({ versions }: { versions: DecisionVersionRead[] }) {
  return (
    <ol className="grid gap-4">
      {versions.map((version, index) => {
        const isFirst = index === 0;
        const isLast = index === versions.length - 1;
        const isCurrent = isLast;
        const isOriginal = isFirst && !isLast;

        return (
          <li key={version.id} className="flex gap-3">
            <div className="flex flex-col items-center pt-1">
              <StatusDot
                variant={isCurrent ? "confirmed" : isOriginal ? "idle" : "attention"}
                hollow={isOriginal}
              />
              {!isLast && (
                <span
                  aria-hidden
                  className="mt-1 flex-1"
                  style={{ width: 1, background: "var(--color-hairline)" }}
                />
              )}
            </div>

            <div className="min-w-0 flex-1 pb-2">
              <div className="flex items-center gap-2">
                <span
                  className="text-[var(--color-ink-strong)]"
                  style={{ fontSize: "var(--text-rowTitle)", fontWeight: "var(--text-rowTitle-weight)" }}
                >
                  {version.current_statement}
                </span>
                <span
                  className="text-[var(--color-ink-muted)]"
                  style={{ fontSize: "var(--text-metaSmall)", fontFamily: "var(--font-mono)" }}
                >
                  {CHANGE_LABEL[version.change_type]}
                </span>
              </div>

              <div
                className="mt-1 text-[var(--color-ink-muted)]"
                style={{ fontSize: "var(--text-metaSmall)" }}
              >
                {version.meeting_id} · {version.created_at.slice(0, 10)}
              </div>

              {!isOriginal && version.previous_statement !== null && (
                <div className="mt-2 grid gap-2">
                  <Quote>{version.previous_statement}</Quote>
                  <ReasonBlock version={version} />
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Names who was absent when the decision changed and what the NLI model
 * concluded — the block S22 puts on every ochre node. Empty absentee list
 * still renders (nobody missing is itself informative here), but the block
 * disappears entirely once the predecessor expired: the router already
 * blanked `previous_statement` in that case, so this never runs without one.
 */
function ReasonBlock({ version }: { version: DecisionVersionRead }) {
  const parts: string[] = [];
  if (version.nli_label !== null) parts.push(`NLI: ${NLI_LABEL[version.nli_label]}`);
  if (version.key_stakeholders_absent.length > 0) {
    parts.push(`불참: ${version.key_stakeholders_absent.join(", ")}`);
  }
  if (parts.length === 0) return null;

  return (
    <p
      className="text-[var(--color-signal-attention)]"
      style={{ fontSize: "var(--text-metaSmall)" }}
    >
      {parts.join(" · ")}
    </p>
  );
}
