"use client";

import { useState } from "react";
import type { ReactNode } from "react";

import { Button, Tabs } from "@/shared/ui";

import { useGapReport } from "../hooks/useGapReport";
import { useTemplateComparison } from "../hooks/useTemplateComparison";
import { useTopicGraph } from "../hooks/useTopicGraph";
import { GapList, lowCount } from "./GapList";
import { TemplateRail } from "./TemplateRail";
import { TopicRanking } from "./TopicRanking";
import { TopicRelations } from "./TopicRelations";

type Tab = "gaps" | "topics";

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
 * **Three independent reads, three independent states.** The report, the topic
 * graph and the template rail are separate endpoints and separate hooks, so a
 * graph that 404s leaves the gap list on screen and vice versa. The first
 * version gated on both reads at once and then handed each section only its
 * data, which made a graph that was still arriving — the common case, since the
 * requests do not land together — render as "토픽 간 관계가 확인되지
 * 않았습니다". A section that has not loaded and a section that is genuinely
 * empty are different sentences, and only the screen holds what tells them
 * apart. Raised in review of #264.
 *
 * **The mockup's meeting tab strip (요약 · 액션 · 갭 · 맥락 · 전사) is not
 * here.** That strip belongs to S15, which is assembled from five features;
 * four of its tabs are other modules' screens and this feature may not reach
 * them. What it does carry is the two views module C owns — the findings and
 * the graph behind them — so the tab band sits where the design puts it
 * without any tab on it being a control that cannot work.
 */
export function GapReportScreen({ meetingId }: { meetingId: string }) {
  const { report, loading: reportLoading, error: reportError } = useGapReport(meetingId);
  const { graph, loading: graphLoading, error: graphError } = useTopicGraph(meetingId);
  const { comparison, loading: railLoading, error: railError } = useTemplateComparison(meetingId);

  const [tab, setTab] = useState<Tab>("gaps");
  const [showLow, setShowLow] = useState(false);

  const gaps = report?.gaps ?? [];
  const low = lowCount(gaps);

  return (
    <main
      className="mx-auto flex min-h-screen flex-col"
      style={{ maxWidth: "var(--layout-canvas)" }}
    >
      <TopBar meetingId={meetingId}>
        {low > 0 ? (
          <Button tone="text" size="default" onClick={() => setShowLow((shown) => !shown)}>
            {showLow ? "LOW 숨기기" : `LOW ${low}건 보기`}
          </Button>
        ) : null}
        {/* The one primary on the screen, and it is not wired: the Slack
            question card is a surface this module has not built (#36). */}
        <Button tone="primary" disabled title="아직 연결되지 않은 동작입니다">
          질문 카드 Slack 전송
        </Button>
      </TopBar>

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
            <ReadSection
              heading="이 회의에서 빠진 논의"
              note={comparison ? `도메인 템플릿 "${comparison.name}" 대조 · 리스크 순` : undefined}
              data={report}
              loading={reportLoading}
              error={reportError}
            >
              {/* `gaps` carries `default_factory=list`, so the contract marks it
                  optional and a report can arrive without the key. */}
              {(loaded) => <GapList gaps={loaded.gaps ?? []} showLow={showLow} />}
            </ReadSection>
          ) : (
            <div className="flex flex-col" style={{ gap: "var(--space-32)" }}>
              <ReadSection heading="토픽" data={report} loading={reportLoading} error={reportError}>
                {(loaded) => <TopicRanking topics={loaded.topics ?? []} />}
              </ReadSection>

              <ReadSection
                heading="토픽 관계"
                data={graph}
                loading={graphLoading}
                error={graphError}
              >
                {(loaded) => <TopicRelations graph={loaded} />}
              </ReadSection>
            </div>
          )}
        </div>

        <aside
          style={{
            background: "var(--color-surface-paper)",
            padding: "var(--space-24)",
          }}
        >
          <ReadSection data={comparison} loading={railLoading} error={railError}>
            {(loaded) => <TemplateRail comparison={loaded} />}
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
 * The meeting's title is not here, and the id is. `/api/gap` answers about one
 * meeting's gaps; the title lives on the shared `meetings` row and reaching for
 * it from this feature would be this screen deciding how a meeting is named
 * across the product. S15 owns the breadcrumb when it exists.
 */
function TopBar({ meetingId, children }: { meetingId: string; children: ReactNode }) {
  return (
    <header
      className="flex items-center border-b border-[var(--color-hairline)]"
      style={{
        height: "var(--space-topbar)",
        gap: "var(--space-12)",
        paddingInline: "var(--space-page)",
      }}
    >
      {/* Below `sm` the actions need the whole bar, and a flex row with nothing
          allowed to shrink just clips every label to a few pixels. What the
          breadcrumb says is recoverable — the words around the id are constant
          on this screen — so they are dropped and the id, which is not, keeps
          the room and truncates. */}
      <span
        className="hidden shrink-0 whitespace-nowrap text-[var(--color-ink-muted)] sm:inline"
        style={{ fontSize: "var(--text-meta)" }}
      >
        회의
      </span>
      <span className="hidden shrink-0 text-[var(--color-signal-idle)] sm:inline">/</span>
      <span
        className="min-w-0 truncate text-[var(--color-ink-strong)]"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "var(--text-dataSmall)",
        }}
      >
        {meetingId}
      </span>
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
