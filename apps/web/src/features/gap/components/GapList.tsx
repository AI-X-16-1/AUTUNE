"use client";

import Link from "next/link";
import type { Route } from "next";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { Button, MaskedText, Quote, ScoreLabel, StatusDot } from "@/shared/ui";

import { COVERAGE_LABELS, SEVERITY_LABELS, bySeverity } from "../types";
import type {
  Gap,
  GapAskTargets,
  GapEvidence,
  GapExplanation,
  GapExplanations,
  ScoreBreakdown,
} from "../types";

/**
 * The gap list on S20: HIGH opened, MEDIUM and LOW closed, LOW behind a toggle.
 *
 * The hierarchy is C's metric made visible. Precision, not recall — a false gap
 * costs user trust and a missed one costs nothing the team did not already
 * have — so only HIGH is opened by default and LOW is not on the screen until
 * somebody asks for it.
 *
 * **Every card is the same card.** What differs by severity is whether it
 * starts open, never what it holds: the verdict, why it was reached, the
 * question that would close it, how the score was reached, and the same three
 * actions. A MEDIUM gap with no question and no "해당 없음" read as a lesser
 * kind of finding rather than as a less certain one.
 *
 * **The counts add up on screen.** The tab counts every gap in the report; the
 * list says how many it shows and how many LOW it is hiding, so the two never
 * disagree silently.
 *
 * The explanation is a second read (`/explanations`) and optional: without it
 * a card still shows its title, score and question, and says it could not load
 * why.
 */
export function GapList({
  gaps,
  explanations = null,
  meetingId,
  showLow = false,
  onToggleLow,
  onDismiss,
  loadAskTargets,
  onAsk,
  pendingGapId = null,
}: {
  gaps: readonly Gap[];
  explanations?: GapExplanations | null;
  /** Links each quote to its place in the transcript tab. */
  meetingId?: string;
  showLow?: boolean;
  onToggleLow?: () => void;
  /** "해당 없음". Without it the button is drawn disabled. */
  onDismiss?: (gapId: string) => void;
  /** "담당자 지정해 질문": the team to pick from, and the pick. Without both the button is drawn disabled. */
  loadAskTargets?: (gapId: string) => Promise<GapAskTargets>;
  onAsk?: (gapId: string, userId: string) => void;
  /** The gap whose write is in flight, so only its button shows it. */
  pendingGapId?: string | null;
}) {
  if (gaps.length === 0) {
    return <EmptyGaps />;
  }

  const high = bySeverity(gaps, "high");
  const medium = bySeverity(gaps, "medium");
  const low = bySeverity(gaps, "low");
  const explained = new Map(explanations?.gaps.map((e) => [e.gap_id, e]) ?? []);
  const shown = [...high, ...medium, ...(showLow ? low : [])];

  // Every gap is LOW and LOW is hidden: without this the list drew nothing at
  // all, which reads as a broken screen rather than as "nothing above the
  // threshold". Seen on a real recording whose five gaps were all LOW.
  if (shown.length === 0) {
    return <OnlyLowGaps count={low.length} onToggleLow={onToggleLow} />;
  }

  const card = (gap: Gap, open: boolean) => (
    <GapCard
      key={gap.id}
      gap={gap}
      explanation={explained.get(gap.id) ?? null}
      explanations={explanations}
      meetingId={meetingId}
      defaultOpen={open}
      onDismiss={onDismiss}
      loadAskTargets={loadAskTargets}
      onAsk={onAsk}
      pending={pendingGapId === gap.id}
    />
  );

  return (
    <div className="flex flex-col" style={{ gap: "var(--space-16)" }}>
      <ListSummary
        shown={shown}
        explained={explained}
        hiddenLow={showLow ? 0 : low.length}
        lowCount={low.length}
        showLow={showLow}
        onToggleLow={onToggleLow}
      />

      {high.length > 0 ? (
        <section className="border-t border-[var(--color-hairline)]">
          {high.map((gap) => card(gap, true))}
        </section>
      ) : null}

      {medium.length > 0 ? (
        <section>
          <SectionTitle>{SEVERITY_LABELS.medium}</SectionTitle>
          <div className="border-t border-[var(--color-hairline)]">
            {medium.map((gap) => card(gap, false))}
          </div>
        </section>
      ) : null}

      {showLow && low.length > 0 ? (
        <section>
          <SectionTitle>{SEVERITY_LABELS.low}</SectionTitle>
          <div className="border-t border-[var(--color-hairline)]">
            {low.map((gap) => card(gap, false))}
          </div>
        </section>
      ) : null}

      <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
        &quot;담당자 지정해 질문&quot;은 고른 팀원을 멘션해 팀 Slack 채널에 질문을
        올립니다. &quot;해당 없음&quot;은 오탐으로 표시합니다. 다음 회의로 넘기는 것은 템플릿 대조 옆의 &quot;다음
        회의 잡기&quot;에서 이 회의의 열린 갭을 한꺼번에 합니다.
      </p>
    </div>
  );
}

/** How many gaps sit below the default threshold. */
export function lowCount(gaps: readonly Gap[]): number {
  return bySeverity(gaps, "low").length;
}

/**
 * What the list shows, in numbers that add up to the tab's: how many cards,
 * how many of them are 누락 and 미흡, and how many LOW are hidden.
 */
function ListSummary({
  shown,
  explained,
  hiddenLow,
  lowCount: lows,
  showLow,
  onToggleLow,
}: {
  shown: readonly Gap[];
  explained: ReadonlyMap<string, GapExplanation>;
  hiddenLow: number;
  lowCount: number;
  showLow: boolean;
  onToggleLow?: () => void;
}) {
  const coverages = shown.map((gap) => explained.get(gap.id)?.coverage ?? null);
  const missing = coverages.filter((c) => c === "missing").length;
  const partial = coverages.filter((c) => c === "partial").length;
  // Only a list that holds both says how it divides. Under the 누락 or 미흡
  // tab every card is one verdict, and "미흡 0" beside the 누락 tab reads as
  // a second, contradicting count.
  const known = missing + partial === shown.length && missing > 0 && partial > 0;

  return (
    <div
      className="flex flex-wrap items-center text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-metaSmall)", gap: "var(--space-8)" }}
    >
      <span>
        표시 {shown.length}건
        {known ? ` · ${COVERAGE_LABELS.missing} ${missing} · ${COVERAGE_LABELS.partial} ${partial}` : ""}
        {hiddenLow > 0 ? ` · ${SEVERITY_LABELS.low} ${hiddenLow}건 숨김` : ""}
      </span>
      {lows > 0 && onToggleLow ? (
        <Button tone="text" size="compact" onClick={onToggleLow}>
          {showLow ? `${SEVERITY_LABELS.low} 숨기기` : `${SEVERITY_LABELS.low} ${lows}건 보기`}
        </Button>
      ) : null}
    </div>
  );
}

/**
 * One gap. The header toggles it; HIGH starts open.
 *
 * "해당 없음" is a real write: `POST /gaps/{id}/dismiss` marks the gap a false
 * positive, it leaves the report, and the rail keeps the item marked — which is
 * where it can be taken back.
 *
 * Sending gaps on to the next meeting is not a card action: "다음 회의 잡기"
 * beside the template rail does it for the whole meeting (#824). The card only
 * says a gap was sent on, from the explanation.
 *
 * "담당자 지정해 질문" opens a picker of the meeting's team, and posts the
 * question on the team's Slack channel mentioning the member picked. It writes
 * nobody's calendar: one person's grant is for their own work only (mkkim68
 * on #824).
 */
function GapCard({
  gap,
  explanation,
  explanations,
  meetingId,
  defaultOpen,
  onDismiss,
  loadAskTargets,
  onAsk,
  pending,
}: {
  gap: Gap;
  explanation: GapExplanation | null;
  explanations: GapExplanations | null;
  meetingId?: string;
  defaultOpen: boolean;
  onDismiss?: (gapId: string) => void;
  loadAskTargets?: (gapId: string) => Promise<GapAskTargets>;
  onAsk?: (gapId: string, userId: string) => void;
  pending: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [asking, setAsking] = useState(false);
  const canAsk = Boolean(loadAskTargets && onAsk);
  const coverage = explanation?.coverage ?? null;
  const carried = explanation?.carried ?? false;

  return (
    <article className="border-b border-[var(--color-hairline)]" style={{ padding: "14px 0" }}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="grid w-full text-left"
        style={{ gridTemplateColumns: "auto 1fr", gap: "var(--space-12)" }}
      >
        <StatusDot variant={DOT[gap.severity]} className="mt-2" />
        {/* The score wraps under the title when the row is narrow, rather than
            squeezing the title to a character per line. */}
        <span
          className="flex min-w-0 flex-wrap items-start justify-between"
          style={{ columnGap: "var(--space-12)", rowGap: "var(--space-4)" }}
        >
          <span className="min-w-0 flex-1" style={{ flexBasis: "12rem" }}>
            <span
              className="block text-[var(--color-ink-strong)]"
              style={{
                fontSize: "var(--text-rowTitle)",
                fontWeight: "var(--text-rowTitle-weight)",
              }}
            >
              <MaskedText>{gap.title}</MaskedText>
            </span>
            {gap.template_item ? (
              <span
                className="block text-[var(--color-ink-muted)]"
                style={{ fontSize: "var(--text-metaSmall)" }}
              >
                템플릿 항목 &quot;{gap.template_item}&quot;
                {coverage ? (
                  <>
                    {" · "}
                    <span style={{ color: COVERAGE_COLOR[coverage] }}>
                      {COVERAGE_LABELS[coverage]}
                    </span>
                  </>
                ) : null}
              </span>
            ) : null}
          </span>
          <ScoreLabel level={gap.severity} score={gap.risk_score} />
        </span>
      </button>

      {carried ? (
        <p
          className="text-[var(--color-accent-hover)]"
          style={{ fontSize: "var(--text-metaSmall)", padding: "4px 0 0 20px" }}
        >
          다음 회의로 넘김
        </p>
      ) : null}

      {open ? (
        <div className="flex flex-col" style={{ gap: "var(--space-12)", padding: "12px 0 0 20px" }}>
          <Block label="판정 근거">
            {explanation ? (
              <Reason explanation={explanation} explanations={explanations} meetingId={meetingId} />
            ) : (
              <Muted>판정 근거를 불러오지 못했습니다.</Muted>
            )}
          </Block>

          {gap.suggested_question ? (
            <Block label="해소용 질문" sunken>
              <p
                className="text-[var(--color-ink-strong)]"
                style={{
                  fontSize: "var(--text-rowBody)",
                  lineHeight: "var(--text-rowBody-leading)",
                }}
              >
                <MaskedText>{gap.suggested_question}</MaskedText>
              </p>
            </Block>
          ) : null}

          <ScoreExplain
            breakdown={explanation?.breakdown ?? null}
            coverage={coverage}
            explanations={explanations}
          />

          <div className="-ml-2 flex flex-wrap" style={{ gap: "var(--space-4)" }}>
            <Button
              tone="text"
              size="compact"
              disabled={!canAsk || pending}
              aria-expanded={asking}
              title={canAsk ? ASK_HINT : PENDING}
              onClick={() => setAsking((value) => !value)}
            >
              담당자 지정해 질문
            </Button>
            <Button
              tone="text"
              size="compact"
              disabled={!onDismiss || pending}
              aria-busy={pending || undefined}
              title={onDismiss ? UNDO_HINT : PENDING}
              onClick={() => onDismiss?.(gap.id)}
            >
              {pending ? "처리 중" : "해당 없음"}
            </Button>
          </div>

          {asking && loadAskTargets && onAsk ? (
            <AskPicker
              gapId={gap.id}
              load={loadAskTargets}
              pending={pending}
              onSend={(userId) => {
                onAsk(gap.id, userId);
                setAsking(false);
              }}
            />
          ) : null}

        </div>
      ) : null}
    </article>
  );
}

/** The member "담당자 지정해 질문" mentions, picked by hand from the meeting's team. */
function AskPicker({
  gapId,
  load,
  pending,
  onSend,
}: {
  gapId: string;
  load: (gapId: string) => Promise<GapAskTargets>;
  pending: boolean;
  onSend: (userId: string) => void;
}) {
  const [targets, setTargets] = useState<GapAskTargets | null>(null);
  const [failed, setFailed] = useState(false);
  const [chosen, setChosen] = useState("");

  useEffect(() => {
    let live = true;
    load(gapId).then(
      (loaded) => {
        if (live) setTargets(loaded);
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [gapId, load]);

  if (failed) return <Muted>팀원 목록을 불러오지 못했습니다.</Muted>;
  if (!targets) return <Muted>팀원 목록을 불러오는 중입니다.</Muted>;

  return (
    <div className="flex flex-wrap items-center" style={{ gap: "var(--space-8)" }}>
      <label className="sr-only" htmlFor={`ask-${gapId}`}>
        질문할 담당자
      </label>
      <select
        id={`ask-${gapId}`}
        value={chosen}
        onChange={(event) => setChosen(event.target.value)}
        className="rounded-[var(--radius)] border border-[var(--color-hairline)] bg-[var(--color-surface-panel)] text-[var(--color-ink-strong)]"
        style={{ fontSize: "var(--text-metaSmall)", padding: "4px 8px" }}
      >
        <option value="">담당자 선택</option>
        {targets.members.map((member) => (
          <option key={member.user_id} value={member.user_id}>
            {member.name}
          </option>
        ))}
      </select>
      <Button
        tone="text"
        size="compact"
        disabled={!chosen || pending}
        onClick={() => onSend(chosen)}
      >
        Slack으로 질문 보내기
      </Button>
    </div>
  );
}

const PENDING = "아직 준비 중인 동작입니다";

const ASK_HINT = "팀원 한 명을 골라, 그 사람을 멘션해 팀 Slack 채널에 해소용 질문을 올립니다.";

const UNDO_HINT = "오탐으로 표시합니다. 오른쪽 템플릿 대조에서 되돌릴 수 있습니다.";

const DOT = { high: "critical", medium: "attention", low: "idle" } as const;

const COVERAGE_COLOR = {
  covered: "var(--color-ink-muted)",
  partial: "var(--color-signal-attention)",
  missing: "var(--color-signal-critical)",
} as const;

/**
 * Why the verdict was reached, in the terms `detect.classify` used, and the
 * utterances it rests on. A missing item has nothing to quote by definition —
 * nothing matched — so it says what the meeting was searched for instead.
 */
function Reason({
  explanation,
  explanations,
  meetingId,
}: {
  explanation: GapExplanation;
  explanations: GapExplanations | null;
  meetingId?: string;
}) {
  const threshold = explanations?.partial_centrality;
  const sentence = (() => {
    switch (explanation.basis) {
      case "topic":
        return (
          `"${explanation.topic_label ?? ""}" 토픽으로 다뤄졌지만 회의에서의 비중(중심도 ` +
          `${fixed(explanation.topic_centrality)})이 기준 ${fixed(threshold)}보다 낮아 ` +
          `미흡으로 판정했습니다.`
        );
      case "keyword":
        return (
          `${quoted(explanation.matched_keywords)}라는 말은 나왔지만 하나의 토픽으로 ` +
          `논의되지는 않아 미흡으로 판정했습니다.`
        );
      case "meaning":
        return (
          "의미가 가까운 발화는 있었지만 토픽으로 논의되지는 않아 미흡으로 판정했습니다. " +
          "의미 비교는 발화를 저장하지 않아 인용할 수 없습니다."
        );
      default:
        // Missing means no topic matched and no keyword was said (and, with the
        // embedder on, no utterance was near the item's examples). Name the two
        // checks every run makes; whether the embedder ran is not sent.
        return (
          `이 항목에 해당하는 토픽이 없었고, 이 항목을 가리키는 표현(` +
          `${quoted(explanation.keywords.slice(0, 5))} 등)도 회의에서 한 번도 나오지 않아 ` +
          `누락으로 판정했습니다.`
        );
    }
  })();

  return (
    <div className="flex flex-col" style={{ gap: "var(--space-8)" }}>
      <p
        className="text-[var(--color-ink-body)]"
        style={{ fontSize: "var(--text-body)", lineHeight: "var(--text-body-leading)" }}
      >
        {sentence}
      </p>
      {explanation.evidence.length > 0 ? (
        <ul className="flex flex-col" style={{ gap: "var(--space-4)" }}>
          {explanation.evidence.map((quote) => (
            <li key={quote.utterance_id}>
              <EvidenceQuote quote={quote} meetingId={meetingId} />
            </li>
          ))}
        </ul>
      ) : explanation.basis === "none" ? (
        <Muted>관련 발화가 없습니다.</Muted>
      ) : null}
    </div>
  );
}

/** One quote, its time first. The time links to the transcript tab. */
export function EvidenceQuote({ quote, meetingId }: { quote: GapEvidence; meetingId?: string }) {
  const time = clock(quote.start_sec);
  return (
    <Quote>
      {meetingId ? (
        <Link
          href={`/meetings/${meetingId}#${quote.utterance_id}` as Route}
          className="mr-2 text-[var(--color-accent-default)] hover:underline"
          style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-dataSmall)" }}
          title="전사에서 이 발화 보기"
        >
          {time}
        </Link>
      ) : (
        <span
          className="mr-2 text-[var(--color-ink-muted)]"
          style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-dataSmall)" }}
        >
          {time}
        </span>
      )}
      <MaskedText>{quote.text}</MaskedText>
    </Quote>
  );
}

const PART_LABELS: Record<string, string> = {
  template: "항목 중요도",
  coverage: "다룬 정도 부족 (1 − 토픽 중심도)",
  participation: "토픽에서 말하지 않은 사람 비율",
};

/**
 * The score's arithmetic, exactly as `detect.score_breakdown` did it: a
 * weighted mean of what was measured, damped when partial. The server sends it
 * only when it still adds up to the stored score.
 */
function ScoreExplain({
  breakdown,
  coverage,
  explanations,
}: {
  breakdown: ScoreBreakdown | null;
  coverage: string | null;
  explanations: GapExplanations | null;
}) {
  return (
    <details>
      <summary
        className="cursor-pointer text-[var(--color-accent-default)]"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        점수 근거
      </summary>
      <div
        className="mt-2 text-[var(--color-ink-body)]"
        style={{ fontSize: "var(--text-metaSmall)", lineHeight: "var(--text-metaSmall-leading)" }}
      >
        {breakdown ? <Arithmetic breakdown={breakdown} coverage={coverage} /> : (
          <Muted>
            점수를 매긴 뒤 점수에 쓰인 값(가중치 설정, 템플릿, 토픽 참여 정보)이 바뀌어 지금
            계산하면 저장된 점수가 나오지 않습니다. 회의가 다시 채점되면 근거가 다시 표시됩니다.
          </Muted>
        )}
        {explanations ? (
          <p className="mt-1 text-[var(--color-ink-muted)]">
            {fixed(explanations.high_threshold)} 이상 높음 · {fixed(explanations.medium_threshold)}{" "}
            이상 중간 · 그 아래 낮음
          </p>
        ) : null}
      </div>
    </details>
  );
}

function Arithmetic({ breakdown, coverage }: { breakdown: ScoreBreakdown; coverage: string | null }) {
  const total = breakdown.parts.reduce((sum, part) => sum + part.weight, 0);
  const mean = breakdown.parts.reduce((sum, part) => sum + part.weight * part.value, 0) / total;
  // One part is its own mean: a weight beside it reads as a multiplication
  // that the score then does not show.
  const weighted = breakdown.parts.length > 1;
  return (
    <>
      {coverage === "missing" ? (
        <p>
          회의에서 관련 내용이 확인되지 않아, 해당 항목의 중요도를 기준으로 점수를 계산합니다.
        </p>
      ) : (
        <p>측정된 값의 가중 평균{breakdown.damping !== null ? "에 미흡 감쇠를 곱한 값" : ""}입니다.</p>
      )}
      <table className="mt-1" style={{ fontFamily: "var(--font-mono)" }}>
        <tbody>
          {breakdown.parts.map((part) => (
            <tr key={part.key}>
              <td className="pr-3" style={{ fontFamily: "var(--font-sans)" }}>
                {PART_LABELS[part.key] ?? part.key}
              </td>
              <td className="pr-3 text-right">{fixed(part.value)}</td>
              <td className="text-[var(--color-ink-muted)]">
                {weighted ? `× 가중치 ${fixed(part.weight)}` : null}
              </td>
            </tr>
          ))}
          {weighted ? (
            <tr>
              <td className="pr-3" style={{ fontFamily: "var(--font-sans)" }}>
                가중 평균
              </td>
              <td className="pr-3 text-right">{fixed(mean)}</td>
              <td />
            </tr>
          ) : null}
          {breakdown.damping !== null ? (
            <tr>
              <td className="pr-3" style={{ fontFamily: "var(--font-sans)" }}>
                미흡 감쇠
              </td>
              <td className="pr-3 text-right">× {fixed(breakdown.damping)}</td>
              <td />
            </tr>
          ) : null}
          <tr className="text-[var(--color-ink-strong)]">
            <td className="pr-3" style={{ fontFamily: "var(--font-sans)" }}>
              점수
            </td>
            <td className="pr-3 text-right">{fixed(breakdown.score)}</td>
            <td />
          </tr>
        </tbody>
      </table>
    </>
  );
}

function Block({
  label,
  sunken = false,
  children,
}: {
  label: string;
  sunken?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      style={
        sunken
          ? {
              background: "var(--color-surface-sunken)",
              borderRadius: "var(--radius)",
              padding: "12px 14px",
            }
          : undefined
      }
    >
      <div
        className="mb-1 text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-label)", fontWeight: "var(--text-label-weight)" }}
      >
        {label}
      </div>
      {children}
    </div>
  );
}

function Muted({ children }: { children: ReactNode }) {
  return (
    <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
      {children}
    </p>
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

/** Two places, as the score is shown everywhere else. */
function fixed(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : value.toFixed(2);
}

function quoted(words: readonly string[]): string {
  return words.map((word) => `"${word}"`).join(", ");
}

/** `m:ss`, or `h:mm:ss` past an hour. */
export function clock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/** Nothing above the threshold, and the LOW ones hidden: say where they are. */
function OnlyLowGaps({ count, onToggleLow }: { count: number; onToggleLow?: () => void }) {
  return (
    <div className="flex flex-wrap items-center" style={{ gap: "var(--space-8)" }}>
      <Muted>기본 기준 이상인 갭은 없습니다. 낮음으로 분류된 갭이 {count}건 있습니다.</Muted>
      {onToggleLow ? (
        <Button tone="text" size="compact" onClick={onToggleLow}>
          낮음 {count}건 보기
        </Button>
      ) : null}
    </div>
  );
}

/**
 * No gaps, said without claiming the meeting had none.
 *
 * A report can be empty because the meeting covered its checklist, because the
 * pipeline has not run, or because the topic graph came out empty — which says
 * extraction found nothing, not that the meeting discussed nothing, and raises
 * no gaps by design. The rail beside this says whether anything was compared.
 */
function EmptyGaps() {
  return (
    <Muted>
      이 회의에서 확인된 갭이 없습니다. 오른쪽 템플릿 대조가 실제로 무엇이 확인되었는지 보여줍니다.
    </Muted>
  );
}

