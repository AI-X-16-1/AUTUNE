"use client";

import type { ReactNode } from "react";

import { useGapReport } from "../hooks/useGapReport";
import { useTopicGraph } from "../hooks/useTopicGraph";
import { GapList } from "./GapList";
import { TopicRanking } from "./TopicRanking";
import { TopicRelations } from "./TopicRelations";

/**
 * S20, the gap report for one meeting.
 *
 * The screen lives in the feature rather than in the route file: `apps/` is
 * assembly, and a page that knew which sections a gap report has and in which
 * order would be module C's screen kept in the team's shared tree. The route
 * mounts this and passes a meeting id.
 *
 * **The two reads are independent, and so is each section's state.** The report
 * and the topic graph are separate endpoints and separate hooks, so a graph that
 * 404s leaves the gap list on screen and vice versa. The first version gated on
 * both reads at once and then handed each section only its data, which made a
 * graph that was still arriving — the common case, since the two requests do not
 * land together — render as "이 회의에서 토픽 간 관계가 확인되지 않았습니다".
 * A section that has not loaded and a section that is genuinely empty are
 * different sentences, and only the screen holds what tells them apart.
 * Raised in review of #264.
 */
export function GapReportScreen({ meetingId }: { meetingId: string }) {
  const { report, loading: reportLoading, error: reportError } = useGapReport(meetingId);
  const { graph, loading: graphLoading, error: graphError } = useTopicGraph(meetingId);

  return (
    <Shell meetingId={meetingId}>
      <div className="flex flex-col gap-8">
        <ReadSection heading="갭" data={report} loading={reportLoading} error={reportError}>
          {/* `gaps` and `topics` carry `default_factory=list`, so the contract
              marks them optional and a report can arrive without the key. */}
          {(loaded) => <GapList gaps={loaded.gaps ?? []} />}
        </ReadSection>

        <ReadSection heading="토픽" data={report} loading={reportLoading} error={reportError}>
          {(loaded) => <TopicRanking topics={loaded.topics ?? []} />}
        </ReadSection>

        <ReadSection heading="토픽 관계" data={graph} loading={graphLoading} error={graphError}>
          {(loaded) => <TopicRelations graph={loaded} />}
        </ReadSection>
      </div>
    </Shell>
  );
}

/**
 * One section of the screen, and the state of the read behind it.
 *
 * Data wins over an error on purpose. Both hooks keep the last good answer when
 * a refresh fails — a 500 on one poll is not a reason to blank a report someone
 * is reading — so a section that has something to show keeps showing it and says
 * separately that it could not refresh. Only a section that never loaded turns
 * into the error.
 */
function ReadSection<T>({
  heading,
  data,
  loading,
  error,
  children,
}: {
  heading: string;
  data: T | null;
  loading: boolean;
  error: Error | null;
  children: (loaded: T) => ReactNode;
}) {
  return (
    <section>
      <SectionHeading>{heading}</SectionHeading>
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

function Shell({ meetingId, children }: { meetingId: string; children: ReactNode }) {
  return (
    <main className="mx-auto max-w-[720px]" style={{ padding: "var(--space-page)" }}>
      <h1
        className="text-[var(--color-ink-strong)]"
        style={{
          fontSize: "var(--text-title)",
          fontWeight: "var(--text-title-weight)",
          letterSpacing: "var(--text-title-tracking)",
        }}
      >
        갭 리포트
      </h1>
      <p
        className="mt-2 text-[var(--color-ink-muted)]"
        style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-metaSmall)" }}
      >
        {meetingId}
      </p>

      <div className="mt-6">{children}</div>
    </main>
  );
}

function SectionHeading({ children }: { children: string }) {
  return (
    <h2
      className="mb-2 text-[var(--color-ink-body)]"
      style={{ fontSize: "var(--text-status)", fontWeight: 500 }}
    >
      {children}
    </h2>
  );
}

function Note({ children }: { children: string }) {
  return (
    <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
      {children}
    </p>
  );
}
