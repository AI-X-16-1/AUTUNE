"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Row, StatusDot } from "@/shared/ui";

import { DecisionTimeline } from "./DecisionTimeline";
import { kstDay } from "../dates";
import { useDecisionLineage } from "../hooks/useDecisionLineage";
import { useDecisionThreads } from "../hooks/useDecisionThreads";

/**
 * S22 — decision lineage. Topic list on the left, one thread's timeline on
 * the right. The graph visualisation the ui-spec mentions is Phase 2; this is
 * the MVP list-plus-timeline form.
 *
 * A team with no thread yet gets a word on where threads come from and a way
 * to start a meeting, not "pick one on the left" over an empty list (#1178).
 */
export function DecisionLineagePanel({ teamId }: { teamId: string }) {
  const { threads, loading, error } = useDecisionThreads({ team_id: teamId });
  const [selected, setSelected] = useState<string | null>(null);
  const { lineage, loading: lineageLoading, error: lineageError } = useDecisionLineage(selected);

  // A thread id is only valid within the team it came from; carrying it across
  // a team switch would ask useDecisionLineage for a thread that team never had.
  useEffect(() => {
    setSelected(null);
  }, [teamId]);

  const empty = !loading && !error && threads.length === 0;

  return (
    <div className="grid gap-6 md:grid-cols-[minmax(0,280px)_1fr]">
      <nav aria-label="결정 스레드">
        {loading && (
          <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-rowBody)" }}>
            불러오는 중…
          </p>
        )}
        {error && (
          <p
            className="text-[var(--color-signal-critical)]"
            style={{ fontSize: "var(--text-rowBody)" }}
          >
            목록을 불러오지 못했습니다.
          </p>
        )}
        {threads.map((thread) => (
          <button
            key={thread.thread_id}
            type="button"
            aria-current={thread.thread_id === selected}
            onClick={() => setSelected(thread.thread_id)}
            className="block w-full text-left"
          >
            <Row
              dot={<StatusDot variant={thread.change_type === "reversed" ? "attention" : "idle"} />}
              title={thread.topic_label}
              meta={kstDay(thread.updated_at)}
              selected={thread.thread_id === selected}
            />
          </button>
        ))}
      </nav>

      <section aria-label="타임라인">
        {empty && (
          <div style={{ fontSize: "var(--text-rowBody)" }}>
            <p className="text-[var(--color-ink-strong)]">아직 기록된 결정이 없습니다.</p>
            <p className="mt-2 text-[var(--color-ink-muted)]">
              회의를 분석하면 그 회의에서 내린 결정이 주제별로 여기에 모입니다. 같은 결정을 다음
              회의에서 바꾸거나 거두면, 그 변화가 같은 주제 아래에 이어서 쌓입니다.
            </p>
            <Link
              href="/meetings/new"
              className="mt-3 inline-block text-[var(--color-accent-default)]"
              style={{ fontSize: "var(--text-meta)" }}
            >
              회의 시작하기
            </Link>
          </div>
        )}
        {selected === null && threads.length > 0 && (
          <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-rowBody)" }}>
            왼쪽에서 주제를 선택하세요.
          </p>
        )}
        {lineageLoading && (
          <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-rowBody)" }}>
            불러오는 중…
          </p>
        )}
        {lineageError && !lineageLoading && (
          <p
            className="text-[var(--color-signal-critical)]"
            style={{ fontSize: "var(--text-rowBody)" }}
          >
            타임라인을 불러오지 못했습니다.
          </p>
        )}
        {lineage !== null && !lineageLoading && (
          <>
            <header
              className="mb-4 flex items-baseline gap-2 text-[var(--color-ink-strong)]"
              style={{ fontSize: "var(--text-heading)", fontWeight: "var(--text-heading-weight)" }}
            >
              {lineage.topic_label}
              <span
                className="text-[var(--color-ink-muted)]"
                style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-dataSmall)" }}
              >
                버전 {lineage.versions.length}개
              </span>
            </header>
            <DecisionTimeline versions={lineage.versions} />
          </>
        )}
      </section>
    </div>
  );
}
