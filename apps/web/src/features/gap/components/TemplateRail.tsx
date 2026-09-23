import { StatusDot } from "@/shared/ui";
import type { StatusVariant } from "@/shared/ui";

import { COVERAGE_LABELS } from "../types";
import type { Coverage, TemplateChecklistItem, TemplateComparison } from "../types";

/**
 * The right rail of S20: the checklist the meeting was held to, and how far it
 * got with each item.
 *
 * It is the gap list read from the other end. The list says what is missing;
 * the rail says what the missing items were measured against, which is the
 * thing that makes a gap a claim rather than an opinion — "이 회의에 예외 처리
 * 논의가 없었습니다" only means something next to a checklist that asked for it.
 *
 * **Covered is the absence of a gap, not a stored state.** `gap_gaps` holds
 * only `partial` and `missing`, because a covered item raises nothing; the
 * server reads the absence of a row back as `covered` and this renders it.
 *
 * **A meeting with no topic graph gets no dots at all.** `analysed: false` is
 * the server saying nothing has been compared, and the alternative reading —
 * no gap row, therefore covered — is a full checklist of green dots for a
 * meeting nobody has processed.
 */
export function TemplateRail({ comparison }: { comparison: TemplateComparison }) {
  return (
    <div className="flex flex-col" style={{ gap: "var(--space-24)" }}>
      <section>
        <h2
          className="text-[var(--color-ink-strong)]"
          style={{
            fontSize: "var(--text-heading)",
            fontWeight: "var(--text-heading-weight)",
          }}
        >
          템플릿 대조 · {comparison.name}
        </h2>
        <p
          className="mt-1 text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          {/* The version names every file that contributed, so two meetings
              can be compared only when they were held to the same checklist.
              Monospace because it is an identifier, not prose. */}
          <span style={{ fontFamily: "var(--font-mono)" }}>{comparison.version}</span>
          {comparison.analysed ? null : " · 아직 대조하지 않았습니다"}
        </p>

        <div className="mt-3 border-t border-[var(--color-hairline)]">
          {comparison.items.map((item) => (
            <ChecklistRow key={item.key} item={item} />
          ))}
        </div>

        {comparison.analysed ? null : (
          <p
            className="mt-3 text-[var(--color-ink-muted)]"
            style={{
              fontSize: "var(--text-metaSmall)",
              lineHeight: "var(--text-metaSmall-leading)",
            }}
          >
            이 회의에서는 아직 토픽이 추출되지 않아 어떤 항목도 판정하지 않았습니다. 항목이 모두
            &quot;다룸&quot;이 아니라, 대조가 일어나지 않은 상태입니다.
          </p>
        )}
      </section>

      <TopicDensityPlaceholder />
    </div>
  );
}

/** One checklist item: state on the left, what it asked for, verdict on the right. */
function ChecklistRow({ item }: { item: TemplateChecklistItem }) {
  return (
    <div
      className="grid items-center border-b border-[var(--color-hairline)]"
      style={{
        gridTemplateColumns: "auto 1fr auto",
        gap: "var(--space-8)",
        padding: "10px 0",
      }}
    >
      <StatusDot variant={DOT[item.coverage ?? "unknown"]} hollow={item.coverage === null} />
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
        style={{
          fontSize: "var(--text-metaSmall)",
          color: VERDICT_COLOR[item.coverage ?? "unknown"],
        }}
      >
        {verdict(item)}
      </span>
    </div>
  );
}

/**
 * A dismissed gap keeps its verdict and says so.
 *
 * Somebody pressing "해당 없음" is a judgement about the gap, not evidence that
 * the meeting covered the item — the row stays for threshold tuning (ADR 0006)
 * and promoting the item to 다룸 here would hide the input that tuning reads.
 */
function verdict(item: TemplateChecklistItem): string {
  if (item.coverage === null) return "분석 전";
  const label = COVERAGE_LABELS[item.coverage];
  return item.dismissed ? `${label} · 해당 없음` : label;
}

const DOT: Record<Coverage | "unknown", StatusVariant> = {
  covered: "confirmed",
  partial: "attention",
  missing: "critical",
  unknown: "idle",
};

const VERDICT_COLOR: Record<Coverage | "unknown", string> = {
  covered: "var(--color-ink-muted)",
  partial: "var(--color-signal-attention)",
  missing: "var(--color-signal-critical)",
  unknown: "var(--color-ink-muted)",
};

/**
 * The mockup's second rail block, and why it is empty.
 *
 * `docs/design/ui-spec.md` S20 draws a topic × role density grid. Both halves
 * of it are missing rather than unimplemented: `participants.role` is written
 * by no production code (#22), and the rule that would read it — "a topic no
 * engineer spoke on is riskier" — was removed from `detect.classify` in review
 * of #266 because it could not fire. Drawing the grid from what exists would
 * mean reading per-participant rows, and totalling the participation matrix
 * along a *person* is the speaking ratio `docs/architecture/privacy.md`
 * section 3 keeps private to its subject.
 *
 * So the block says what it is waiting for. A screen that quietly omits it
 * leaves the next reader to rediscover why.
 */
function TopicDensityPlaceholder() {
  return (
    <section>
      <h2
        className="text-[var(--color-ink-strong)]"
        style={{
          fontSize: "var(--text-heading)",
          fontWeight: "var(--text-heading-weight)",
        }}
      >
        토픽별 논의 밀도{" "}
        <span
          className="text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)", fontWeight: 400 }}
        >
          직무 단위
        </span>
      </h2>
      <p
        className="mt-2 text-[var(--color-ink-muted)]"
        style={{
          fontSize: "var(--text-metaSmall)",
          lineHeight: "var(--text-metaSmall-leading)",
        }}
      >
        직무 정보가 아직 기록되지 않아 표시하지 않습니다. 개인별 발언량은 어떤 화면에서도 보여주지
        않으며, 이 표는 직무 단위 집계로만 채워집니다.
      </p>
    </section>
  );
}
