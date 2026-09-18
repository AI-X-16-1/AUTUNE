"use client";

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
 * **The two reads are independent.** The report and the topic graph are
 * separate endpoints and separate hooks, so a graph that 404s still leaves the
 * gap list on screen and vice versa. They are also two reasons the screen can
 * be waiting, which is why the heading area reports loading once and each
 * section says its own emptiness in its own words.
 */
export function GapReportScreen({ meetingId }: { meetingId: string }) {
  const { report, loading: reportLoading, error: reportError } = useGapReport(meetingId);
  const { graph, loading: graphLoading, error: graphError } = useTopicGraph(meetingId);

  // An unknown meeting is the one error both reads agree on, and the only one
  // worth replacing the screen with — anything else leaves the half that
  // answered readable.
  if (reportError && graphError) {
    return (
      <Shell meetingId={meetingId}>
        <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
          이 회의의 분석 결과를 불러오지 못했습니다.
        </p>
      </Shell>
    );
  }

  if (reportLoading && graphLoading) {
    return (
      <Shell meetingId={meetingId}>
        <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
          분석 결과를 불러오는 중입니다.
        </p>
      </Shell>
    );
  }

  return (
    <Shell meetingId={meetingId}>
      <div className="flex flex-col gap-8">
        <section>
          <SectionHeading>갭</SectionHeading>
          <GapList gaps={report?.gaps ?? []} />
        </section>

        <section>
          <SectionHeading>토픽</SectionHeading>
          <TopicRanking topics={report?.topics ?? []} />
        </section>

        <section>
          <SectionHeading>토픽 관계</SectionHeading>
          <TopicRelations graph={graph} />
        </section>
      </div>
    </Shell>
  );
}

function Shell({ meetingId, children }: { meetingId: string; children: React.ReactNode }) {
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
