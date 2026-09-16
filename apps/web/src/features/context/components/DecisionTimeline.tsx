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
 * The last version is always ink ("현재"), position telling you where you are
 * in the timeline regardless of that version's own `change_type`. The first
 * is not the same kind of marker: retention can sweep the earlier versions of
 * a thread out from under it, so `versions[0]` is only sometimes the version
 * that started the thread — `change_type === "new"` is, always (`types.ts`
 * carries the same warning about `previous_statement`). Using position for
 * "원본" would draw a swept-in `reversed` version as a blank ring with no
 * reason block, on the exact thread where a reader most wants one. Raised in
 * review of #204.
 */
export function DecisionTimeline({ versions }: { versions: DecisionVersionRead[] }) {
  return (
    <ol className="grid gap-4">
      {versions.map((version, index) => {
        const isLast = index === versions.length - 1;
        const isCurrent = isLast;
        const isOriginal = version.change_type === "new";

        return (
          <li key={version.id} className="flex gap-3">
            <div className="flex flex-col items-center pt-1">
              <StatusDot
                variant={isCurrent ? "confirmed" : isOriginal ? "idle" : "attention"}
                hollow={isOriginal && !isCurrent}
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

              {!isOriginal && (
                <div className="mt-2 grid gap-2">
                  {version.previous_statement !== null && <Quote>{version.previous_statement}</Quote>}
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
 * concluded — the block S22 puts on every ochre node.
 *
 * Independent of the quote above it: an expired predecessor blanks
 * `previous_statement`, but `nli_label` and `key_stakeholders_absent` are
 * this version's own fields and outlive the meeting they compared against.
 * The reason a decision changed is exactly what a reader still wants once the
 * old wording is gone — dropping it there would make S22 quietest at the
 * moment it matters most.
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
