import { MaskedText, Row, ScoreLabel, StatusDot } from "@/shared/ui";

import { SEVERITY_LABELS, bySeverity } from "../types";
import type { Gap } from "../types";

/**
 * The gap list on S20: HIGH expanded, MEDIUM collapsed, LOW listed apart.
 *
 * The hierarchy is C's metric made visible. Precision, not recall — a false gap
 * costs user trust and a missed one costs nothing the team did not already
 * have — so only HIGH is opened by default and LOW sits below a rule, read
 * only by someone who went looking.
 *
 * Every gap arrives with the question that would close it, and that block is
 * the point of the screen: "성능 요구사항 미정" tells a team it has a problem,
 * "목표 응답 시간을 정하셨나요?" is the thing they can act on in the meeting
 * thread.
 */
export function GapList({ gaps }: { gaps: readonly Gap[] }) {
  if (gaps.length === 0) {
    return <EmptyGaps />;
  }

  const high = bySeverity(gaps, "high");
  const medium = bySeverity(gaps, "medium");
  const low = bySeverity(gaps, "low");

  return (
    <div className="flex flex-col gap-6">
      {high.length > 0 ? (
        <section className="flex flex-col gap-3">
          {high.map((gap) => (
            <ExpandedGap key={gap.id} gap={gap} />
          ))}
        </section>
      ) : null}

      {medium.length > 0 ? (
        <section>
          <SectionTitle>{SEVERITY_LABELS.medium}</SectionTitle>
          {medium.map((gap) => (
            <CollapsedGap key={gap.id} gap={gap} />
          ))}
        </section>
      ) : null}

      {low.length > 0 ? (
        <section>
          <SectionTitle>{SEVERITY_LABELS.low}</SectionTitle>
          {low.map((gap) => (
            <CollapsedGap key={gap.id} gap={gap} />
          ))}
        </section>
      ) : null}
    </div>
  );
}

function ExpandedGap({ gap }: { gap: Gap }) {
  return (
    <article
      className="border"
      style={{
        background: "var(--color-surface-paper)",
        borderColor: "var(--color-hairline)",
        borderRadius: "var(--radius)",
        padding: "var(--space-card)",
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <h3
          className="min-w-0 text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-rowTitle)", fontWeight: "var(--text-rowTitle-weight)" }}
        >
          <MaskedText>{gap.title}</MaskedText>
        </h3>
        <ScoreLabel level={gap.severity} score={gap.risk_score} />
      </div>

      {gap.template_item ? (
        <p
          className="mt-1 text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          템플릿 항목 · {gap.template_item}
        </p>
      ) : null}

      {gap.suggested_question ? (
        <p
          className="mt-3 text-[var(--color-ink-body)]"
          style={{
            background: "var(--color-surface-sunken)",
            borderRadius: "var(--radius)",
            padding: "var(--space-card)",
            fontSize: "var(--text-rowTitle)",
          }}
        >
          <MaskedText>{gap.suggested_question}</MaskedText>
        </p>
      ) : null}
    </article>
  );
}

function CollapsedGap({ gap }: { gap: Gap }) {
  return (
    <Row
      dot={<StatusDot variant={gap.severity === "medium" ? "attention" : "idle"} />}
      title={<MaskedText>{gap.title}</MaskedText>}
      meta={gap.template_item ?? undefined}
      actions={<ScoreLabel level={gap.severity} score={gap.risk_score} />}
    />
  );
}

function SectionTitle({ children }: { children: string }) {
  return (
    <h2
      className="mb-1 text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-metaSmall)" }}
    >
      {children}
    </h2>
  );
}

/**
 * No gaps, said without claiming the meeting had none.
 *
 * A report can be empty because the pipeline found nothing, because it has not
 * run, or — today, for every meeting — because the code that raises a gap is
 * not built yet. "감지된 갭이 없습니다" would read as the first of those, and a
 * team that trusts it once will not read the next report.
 */
function EmptyGaps() {
  return (
    <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
      아직 이 회의에서 확인된 갭이 없습니다. 아래 토픽은 분석이 끝난 결과입니다.
    </p>
  );
}
