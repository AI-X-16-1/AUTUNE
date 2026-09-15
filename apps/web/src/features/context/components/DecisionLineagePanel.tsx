"use client";

import { useState } from "react";

import { Row, StatusDot } from "@/shared/ui";

import { DecisionTimeline } from "./DecisionTimeline";
import { useDecisionLineage } from "../hooks/useDecisionLineage";
import { useDecisionThreads } from "../hooks/useDecisionThreads";

/**
 * S22 — decision lineage. Topic list on the left, one thread's timeline on
 * the right. The graph visualisation the ui-spec mentions is Phase 2; this is
 * the MVP list-plus-timeline form.
 */
export function DecisionLineagePanel({ teamId }: { teamId: string }) {
  const { threads, loading, error } = useDecisionThreads({ team_id: teamId });
  const [selected, setSelected] = useState<string | null>(null);
  const { lineage, loading: lineageLoading } = useDecisionLineage(selected);

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
            onClick={() => setSelected(thread.thread_id)}
            className="block w-full text-left"
          >
            <Row
              dot={<StatusDot variant={thread.change_type === "reversed" ? "attention" : "idle"} />}
              title={thread.topic_label}
              meta={thread.updated_at.slice(0, 10)}
              selected={thread.thread_id === selected}
            />
          </button>
        ))}
      </nav>

      <section aria-label="타임라인">
        {selected === null && (
          <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-rowBody)" }}>
            왼쪽에서 주제를 선택하세요.
          </p>
        )}
        {lineageLoading && (
          <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-rowBody)" }}>
            불러오는 중…
          </p>
        )}
        {lineage !== null && (
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
