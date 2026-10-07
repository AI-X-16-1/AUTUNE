"use client";

import { useCallback, useState } from "react";
import type { ReactNode } from "react";

import { Button, Tabs } from "@/shared/ui";

import { getAgendaEvents, getAskTargets } from "../api";
import { useGapActions } from "../hooks/useGapActions";
import { useGapExplanations } from "../hooks/useGapExplanations";
import { useGapReport } from "../hooks/useGapReport";
import { usePollUntilAnalysed } from "../hooks/usePollUntilAnalysed";
import { useTemplateComparison } from "../hooks/useTemplateComparison";
import { useTemplates } from "../hooks/useTemplates";
import { COVERAGE_LABELS } from "../types";
import type { Coverage, Gap, GapExplanations, TemplateComparison } from "../types";
import { CoveredList } from "./CoveredList";
import { GapList } from "./GapList";
import { TemplateRail } from "./TemplateRail";
import { TopicRanking } from "./TopicRanking";

type Tab = "gaps" | "topics";

/** The order the 갭 tab's own tabs read in: what is worst first. */
const COVERAGES = ["missing", "partial", "covered"] as const satisfies readonly Coverage[];

const COVERAGE_HEADINGS: Record<Coverage, string> = {
  missing: "이 회의에서 빠진 논의",
  partial: "충분히 다루지 못한 논의",
  covered: "충분히 다룬 항목",
};

/**
 * Each gap's verdict, or `null` while any gap's is unknown.
 *
 * The verdict is not on the contract's `Gap`; it comes from the rail
 * (`items[].gap_id`) or from `/explanations`, whichever has arrived. Until
 * every gap has one, the tab cannot sort them honestly — a gap filed under
 * 누락 for want of a verdict is a claim the screen did not have — so the
 * caller shows the one undivided list instead.
 */
function coverageByGap(
  gaps: readonly Gap[],
  comparison: TemplateComparison | null,
  explanations: GapExplanations | null,
): Map<string, Coverage> | null {
  const known = new Map<string, Coverage>();
  for (const e of explanations?.gaps ?? []) {
    if (e.coverage) known.set(e.gap_id, e.coverage);
  }
  for (const item of comparison?.items ?? []) {
    if (item.gap_id && item.coverage) known.set(item.gap_id, item.coverage);
  }
  return gaps.every((gap) => known.has(gap.id)) && comparison !== null ? known : null;
}

/**
 * S20, the gap report for one meeting, laid out as
 * `docs/design/AUTUNE Spec 03 회의 후.dc.html` draws it: a 1280 canvas, a top
 * bar, and the findings beside the checklist they were measured against.
 *
 * The design is drawn on one canvas width and the screen is not: the rail
 * stacks under the findings below `lg`. A fixed 400px column beside a
 * flexible one has no width at which both fit on a phone, and every other
 * screen in the product already stacks rather than shrink one side to
 * nothing.
 *
 * The screen lives in the feature rather than in the route file: `apps/` is
 * assembly, and a page that knew which sections a gap report has and in which
 * order would be module C's screen kept in the team's shared tree. The route
 * mounts this and passes a meeting id.
 *
 * **Independent reads, independent states.** The report, the template rail and
 * the explanations are separate endpoints and separate hooks, so a rail that
 * 404s leaves the gap list on screen and vice versa. A section that has not
 * loaded and a section that is genuinely empty are different sentences, and
 * only the screen holds what tells them apart. Raised in review of #264.
 *
 * **The mockup's meeting tab strip (요약 · 액션 · 갭 · 맥락 · 전사) is not
 * here.** That strip belongs to S15, which is assembled from five features;
 * four of its tabs are other modules' screens and this feature may not reach
 * them. What it does carry is the two views module C owns — the findings and
 * the topics behind them — so the tab band sits where the design puts it
 * without any tab on it being a control that cannot work.
 *
 * **The 토픽 tab ranks topics and draws no relations.** The list of topic
 * pairs and how they relate was a developer's view of the graph, not
 * something a team acts on, so it was taken off the screen. The graph is
 * still built and stored, and `GET /api/gap/topics/{meeting_id}` still serves
 * it for anyone debugging the pipeline.
 *
 * **The 갭 tab is split by verdict: 누락, 미흡, 충족.** 누락 and 미흡 are the
 * report's gaps, sorted by the verdict the rail or `/explanations` carries;
 * 충족 is the rail's covered items, which raise no gap and so have no card.
 * While a verdict is still unknown the tab shows the one undivided list, as
 * it did before the split.
 *
 * **A meeting still being analysed is read again until it is not.** The
 * endpoints answer 200 with nothing for a meeting the pipeline has not reached,
 * so a screen opened straight after an upload would otherwise stay empty until
 * somebody reloaded it. The rail's `analysed` flag decides — see
 * `usePollUntilAnalysed`.
 *
 * **The writes re-read, they do not patch.** Dismissing a gap, taking it back
 * and choosing a template each end in a fresh read of every section,
 * because the server is what decides how a dismissal moves between the list
 * and the rail and what a new checklist raises — see `useGapActions`.
 */
export function GapReportScreen({ meetingId }: { meetingId: string }) {
  const {
    report,
    loading: reportLoading,
    error: reportError,
    reload: reloadReport,
  } = useGapReport(meetingId);
  const {
    comparison,
    loading: railLoading,
    error: railError,
    reload: reloadRail,
  } = useTemplateComparison(meetingId);
  const { explanations, reload: reloadExplanations } = useGapExplanations(meetingId);
  const templates = useTemplates();

  const reloadAll = useCallback(() => {
    void reloadReport();
    void reloadRail();
    void reloadExplanations();
  }, [reloadReport, reloadRail, reloadExplanations]);

  usePollUntilAnalysed(comparison ? comparison.analysed : null, reloadAll);
  const { pending, failure, notice, dismiss, undoDismiss, scheduleNext, ask, saveQuestion, choose } =
    useGapActions(reloadAll);

  const [tab, setTab] = useState<Tab>("gaps");
  const [coverageTab, setCoverageTab] = useState<Coverage>("missing");
  const [showLow, setShowLow] = useState(false);

  const gaps = report?.gaps ?? [];
  const split = coverageByGap(gaps, comparison, explanations);
  const covered =
    comparison?.analysed === true
      ? comparison.items.filter((item) => item.coverage === "covered")
      : [];
  const templateNote = comparison ? `도메인 템플릿 "${comparison.name}" 대조` : undefined;

  return (
    <main
      className="mx-auto flex min-h-screen flex-col"
      style={{ maxWidth: "var(--layout-canvas)" }}
    >
      <TopBar
        title={explanations?.meeting_title ?? null}
        date={explanations?.meeting_date ?? null}
      >
        {/* The one primary on the screen, and it is not wired: the Slack
            question card is a surface this module has not built (#824). A
            disabled button alone does not say why, so the reason is written
            beside it rather than left to a tooltip nobody hovers. */}
        <span
          id="slack-send-status"
          className="hidden text-[var(--color-ink-muted)] md:inline"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          Slack 전송은 준비 중입니다
        </span>
        <Button
          tone="primary"
          disabled
          aria-describedby="slack-send-status"
          title="질문 카드를 Slack으로 보내는 기능은 아직 준비 중입니다"
        >
          질문 카드 Slack 전송
        </Button>
      </TopBar>

      {failure ? (
        <p
          role="alert"
          className="text-[var(--color-signal-critical)]"
          style={{
            fontSize: "var(--text-metaSmall)",
            padding: "var(--space-8) var(--space-page) 0",
          }}
        >
          {failure}
        </p>
      ) : null}

      {notice ? (
        <p
          role="status"
          className="text-[var(--color-ink-muted)]"
          style={{
            fontSize: "var(--text-metaSmall)",
            padding: "var(--space-8) var(--space-page) 0",
          }}
        >
          {notice}
        </p>
      ) : null}

      <div style={{ paddingInline: "var(--space-page)" }}>
        <Tabs<Tab>
          tabs={[
            { id: "gaps", label: "갭", count: gaps.length },
            { id: "topics", label: "토픽", count: report?.topics?.length ?? 0 },
          ]}
          active={tab}
          onChange={setTab}
        />
      </div>

      {/* The rail is 400px wide and does not shrink, so below the `lg`
          breakpoint it is stacked under the findings rather than beside
          them. Held side by side it takes the whole viewport at phone
          width and the findings column collapses to a few dozen pixels —
          `minmax(0, 1fr)` yields the space instead of overflowing. Same
          shape as `context/DecisionLineagePanel`. Raised in review of #303. */}
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_400px]">
        <div
          className="border-b border-[var(--color-hairline)] lg:border-b-0 lg:border-r"
          style={{ padding: "var(--space-24) var(--space-page)" }}
        >
          {tab === "gaps" ? (
            split ? (
              <div className="flex flex-col" style={{ gap: "var(--space-16)" }}>
                <Tabs<Coverage>
                  tabs={COVERAGES.map((coverage) => ({
                    id: coverage,
                    label: COVERAGE_LABELS[coverage],
                    count:
                      coverage === "covered"
                        ? covered.length
                        : gaps.filter((gap) => split.get(gap.id) === coverage).length,
                  }))}
                  active={coverageTab}
                  onChange={setCoverageTab}
                />
                {coverageTab === "covered" ? (
                  <ReadSection
                    heading={COVERAGE_HEADINGS.covered}
                    note={templateNote}
                    data={comparison}
                    loading={railLoading}
                    error={railError}
                  >
                    {() => (
                      <CoveredList
                        items={covered}
                        explanations={explanations}
                        meetingId={meetingId}
                      />
                    )}
                  </ReadSection>
                ) : (
                  <ReadSection
                    heading={COVERAGE_HEADINGS[coverageTab]}
                    note={templateNote && `${templateNote} · 리스크 순`}
                    data={report}
                    loading={reportLoading}
                    error={reportError}
                  >
                    {(loaded) => {
                      const shown = (loaded.gaps ?? []).filter(
                        (gap) => split.get(gap.id) === coverageTab,
                      );
                      return shown.length === 0 ? (
                        <Note>{`${COVERAGE_LABELS[coverageTab]}으로 판정된 항목이 없습니다.`}</Note>
                      ) : (
                        <GapList
                          gaps={shown}
                          explanations={explanations}
                          meetingId={meetingId}
                          showLow={showLow}
                          onToggleLow={() => setShowLow((on) => !on)}
                          onDismiss={(gapId) => void dismiss(gapId)}
                          loadAskTargets={getAskTargets}
                          onAsk={(gapId, userId) => void ask(gapId, userId)}
                          onSaveQuestion={saveQuestion}
                          pendingGapId={pending}
                        />
                      );
                    }}
                  </ReadSection>
                )}
              </div>
            ) : (
              <ReadSection
                heading="이 회의에서 빠진 논의"
                note={templateNote && `${templateNote} · 리스크 순`}
                data={report}
                loading={reportLoading}
                error={reportError}
              >
                {/* `gaps` carries `default_factory=list`, so the contract marks it
                    optional and a report can arrive without the key. */}
                {(loaded) => (
                  <GapList
                    gaps={loaded.gaps ?? []}
                    explanations={explanations}
                    meetingId={meetingId}
                    showLow={showLow}
                    onToggleLow={() => setShowLow((on) => !on)}
                    onDismiss={(gapId) => void dismiss(gapId)}
                    loadAskTargets={getAskTargets}
                    onAsk={(gapId, userId) => void ask(gapId, userId)}
                    onSaveQuestion={saveQuestion}
                    pendingGapId={pending}
                  />
                )}
              </ReadSection>
            )
          ) : (
            <ReadSection heading="토픽" data={report} loading={reportLoading} error={reportError}>
              {(loaded) => <TopicRanking topics={loaded.topics ?? []} />}
            </ReadSection>
          )}
        </div>

        <aside
          style={{
            background: "var(--color-surface-paper)",
            padding: "var(--space-24)",
          }}
        >
          <ReadSection data={comparison} loading={railLoading} error={railError}>
            {(loaded) => (
              <TemplateRail
                comparison={loaded}
                templates={templates}
                onChoose={(templateKey) => void choose(meetingId, templateKey)}
                onUndoDismiss={(gapId) => void undoDismiss(gapId)}
                loadAgendaEvents={() => getAgendaEvents(meetingId)}
                onScheduleNext={(eventId) => void scheduleNext(meetingId, eventId)}
                pending={pending}
              />
            )}
          </ReadSection>
        </aside>
      </div>
    </main>
  );
}

/**
 * The 56px bar the design puts above every content panel: where you are on the
 * left, what you can do on the right, and a hairline under it.
 *
 * Where you are is the meeting's title and date, from module C's own read
 * (`/explanations`), which reads the shared `meetings` row and writes nothing
 * to it. The id it used to show is an identifier, not a place; until the title
 * arrives the bar says "회의" and nothing else.
 */
function TopBar({
  title,
  date: when,
  children,
}: {
  title: string | null;
  /** The meeting's start, or when it was registered if it has none. */
  date: string | null;
  children: ReactNode;
}) {
  const date = when ? DATE.format(new Date(when)) : null;
  return (
    <header
      className="flex items-center border-b border-[var(--color-hairline)]"
      style={{
        height: "var(--space-topbar)",
        gap: "var(--space-12)",
        paddingInline: "var(--space-page)",
      }}
    >
      {/* Below `sm` the actions need the whole bar, so the constant words are
          dropped and the title, which is not constant, keeps the room and
          truncates. */}
      <span
        className="hidden shrink-0 whitespace-nowrap text-[var(--color-ink-muted)] sm:inline"
        style={{ fontSize: "var(--text-meta)" }}
      >
        회의
      </span>
      {title ? (
        <>
          <span className="hidden shrink-0 text-[var(--color-signal-idle)] sm:inline">/</span>
          <span
            className="min-w-0 truncate text-[var(--color-ink-strong)]"
            style={{ fontSize: "var(--text-meta)", fontWeight: 500 }}
          >
            {title}
          </span>
          {date ? (
            <span
              className="hidden shrink-0 whitespace-nowrap text-[var(--color-ink-muted)] sm:inline"
              style={{ fontSize: "var(--text-metaSmall)" }}
            >
              {date}
            </span>
          ) : null}
        </>
      ) : null}
      <span
        className="hidden shrink-0 whitespace-nowrap text-[var(--color-ink-muted)] sm:inline"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        · 갭 리포트
      </span>
      <div className="flex-1" />
      <div className="flex shrink-0 items-center" style={{ gap: "var(--space-12)" }}>
        {children}
      </div>
    </header>
  );
}

const DATE = new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "long", day: "numeric" });

/**
 * One section of the screen, and the state of the read behind it.
 *
 * Data wins over an error on purpose. The hooks keep the last good answer when
 * a refresh fails — a 500 on one poll is not a reason to blank a report someone
 * is reading — so a section that has something to show keeps showing it and says
 * separately that it could not refresh. Only a section that never loaded turns
 * into the error.
 */
function ReadSection<T>({
  heading,
  note,
  data,
  loading,
  error,
  children,
}: {
  heading?: string;
  note?: string;
  data: T | null;
  loading: boolean;
  error: Error | null;
  children: (loaded: T) => ReactNode;
}) {
  return (
    <section>
      {heading ? (
        <div className="mb-3 flex flex-wrap items-baseline" style={{ gap: "var(--space-8)" }}>
          <h2
            className="text-[var(--color-ink-strong)]"
            style={{
              fontSize: "var(--text-heading)",
              fontWeight: "var(--text-heading-weight)",
            }}
          >
            {heading}
          </h2>
          {note ? (
            <span
              className="text-[var(--color-ink-muted)]"
              style={{ fontSize: "var(--text-metaSmall)" }}
            >
              {note}
            </span>
          ) : null}
        </div>
      ) : null}

      {data !== null ? (
        <>
          {children(data)}
          {error ? <Note>최신 결과를 불러오지 못해 이전 결과를 보여주고 있습니다.</Note> : null}
        </>
      ) : error ? (
        <Note>이 회의의 분석 결과를 불러오지 못했습니다.</Note>
      ) : loading ? (
        <Note>분석 결과를 불러오는 중입니다.</Note>
      ) : (
        <Note>분석 결과가 없습니다.</Note>
      )}
    </section>
  );
}

function Note({ children }: { children: string }) {
  return (
    <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
      {children}
    </p>
  );
}
