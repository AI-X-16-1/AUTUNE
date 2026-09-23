import { Button, MaskedText, Row, ScoreLabel, StatusDot } from "@/shared/ui";

import { SEVERITY_LABELS, bySeverity } from "../types";
import type { Gap } from "../types";

/**
 * The gap list on S20: HIGH expanded, MEDIUM collapsed, LOW behind a toggle.
 *
 * The hierarchy is C's metric made visible. Precision, not recall — a false gap
 * costs user trust and a missed one costs nothing the team did not already
 * have — so only HIGH is opened by default and LOW is not on the screen at all
 * until somebody goes looking for it.
 *
 * Every gap arrives with the question that would close it, and that block is
 * the point of the screen: "성능 요구사항 미정" tells a team it has a problem,
 * "목표 응답 시간을 정하셨나요?" is the thing they can act on in the meeting
 * thread.
 */
export function GapList({ gaps, showLow = false }: { gaps: readonly Gap[]; showLow?: boolean }) {
  if (gaps.length === 0) {
    return <EmptyGaps />;
  }

  const high = bySeverity(gaps, "high");
  const medium = bySeverity(gaps, "medium");
  const low = bySeverity(gaps, "low");

  return (
    <div className="flex flex-col" style={{ gap: "var(--space-24)" }}>
      {high.length > 0 ? (
        <section className="border-t border-[var(--color-hairline)]">
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

      {showLow && low.length > 0 ? (
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

/** How many gaps sit below the default threshold, for the toggle in the top bar. */
export function lowCount(gaps: readonly Gap[]): number {
  return bySeverity(gaps, "low").length;
}

/**
 * One HIGH gap, opened: what is missing, how sure we are, and the question
 * that would settle it.
 *
 * The three actions are drawn and disabled. `POST /gaps/{id}/dismiss` and the
 * Slack question card do not exist yet — a button that looked live and did
 * nothing would teach a reader to distrust the rest of the screen, and the
 * dismissal is the input ADR 0006's threshold tuning reads, so it is worth
 * shipping as a real write rather than as a stub.
 */
function ExpandedGap({ gap }: { gap: Gap }) {
  return (
    <article
      className="grid border-b border-[var(--color-hairline)]"
      style={{
        gridTemplateColumns: "auto 1fr",
        gap: "var(--space-12)",
        padding: "18px 0",
      }}
    >
      <StatusDot variant="critical" className="mt-2" />

      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline" style={{ gap: "var(--space-12)" }}>
          <h3
            className="min-w-0 text-[var(--color-ink-strong)]"
            style={{
              fontSize: "var(--text-rowTitle)",
              fontWeight: "var(--text-rowTitle-weight)",
            }}
          >
            <MaskedText>{gap.title}</MaskedText>
          </h3>
          <ScoreLabel level={gap.severity} score={gap.risk_score} />
        </div>

        {gap.template_item ? (
          <p
            className="mt-2 text-[var(--color-ink-body)]"
            style={{
              fontSize: "var(--text-body)",
              lineHeight: "var(--text-body-leading)",
            }}
          >
            템플릿 필수 항목 &quot;{gap.template_item}&quot;.
          </p>
        ) : null}

        {gap.suggested_question ? (
          <div
            className="mt-3"
            style={{
              background: "var(--color-surface-sunken)",
              borderRadius: "var(--radius)",
              padding: "12px 14px",
            }}
          >
            <div
              className="text-[var(--color-ink-muted)]"
              style={{
                fontSize: "var(--text-label)",
                fontWeight: "var(--text-label-weight)",
              }}
            >
              해소용 질문
            </div>
            <p
              className="mt-1 text-[var(--color-ink-strong)]"
              style={{
                fontSize: "var(--text-rowBody)",
                lineHeight: "var(--text-rowBody-leading)",
              }}
            >
              <MaskedText>{gap.suggested_question}</MaskedText>
            </p>
          </div>
        ) : null}

        <div className="mt-2 -ml-2 flex flex-wrap" style={{ gap: "var(--space-4)" }}>
          {/* Text tones rather than the mockup's accent fill: the screen's one
              primary is the Slack send in the top bar, and the ui-spec allows
              at most one per screen. */}
          <Button tone="text" size="compact" disabled title={PENDING}>
            다음 회의 어젠다로
          </Button>
          <Button tone="text" size="compact" disabled title={PENDING}>
            담당자 지정해 질문
          </Button>
          <Button tone="quiet" size="compact" disabled title={PENDING}>
            해당 없음
          </Button>
        </div>
      </div>
    </article>
  );
}

const PENDING = "아직 연결되지 않은 동작입니다";

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
 * A report can be empty because the meeting covered its checklist, because the
 * pipeline has not run, or because the topic graph came out empty — which says
 * extraction found nothing, not that the meeting discussed nothing, and raises
 * no gaps by design. "감지된 갭이 없습니다" reads as the first of those, and a
 * team that trusts it once will not read the next report. The rail beside this
 * is what distinguishes them: it says whether anything was compared at all.
 */
function EmptyGaps() {
  return (
    <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
      이 회의에서 확인된 갭이 없습니다. 오른쪽 템플릿 대조가 실제로 무엇이 확인되었는지 보여줍니다.
    </p>
  );
}
