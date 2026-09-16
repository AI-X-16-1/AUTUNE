"use client";

import { TopicLinkRow } from "./TopicLinkRow";
import { useTopicLinks } from "../hooks/useTopicLinks";

/**
 * S15's "context" tab: this meeting's topic links to past meetings.
 *
 * Pending links need a decision before they count as a link at all — E never
 * sees one until it is `asserted` or `confirmed` (`_PUBLISHABLE` in
 * `service.py`) — so they sit in their own section rather than mixed in,
 * ochre and un-decided rather than settled.
 */
export function ContextTab({ meetingId }: { meetingId: string }) {
  const { asserted, pending, loading, error, decide } = useTopicLinks(meetingId);

  if (loading) {
    return (
      <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-rowBody)" }}>
        불러오는 중…
      </p>
    );
  }

  if (error) {
    return (
      <p className="text-[var(--color-signal-critical)]" style={{ fontSize: "var(--text-rowBody)" }}>
        연결 정보를 불러오지 못했습니다.
      </p>
    );
  }

  if (asserted.length === 0 && pending.length === 0) {
    return (
      <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-rowBody)" }}>
        이 회의와 연결된 과거 회의가 없습니다.
      </p>
    );
  }

  return (
    <div style={{ display: "grid", gap: "var(--space-page)" }}>
      {pending.length > 0 && (
        <section aria-label="확인 필요">
          <header
            className="text-[var(--color-ink-strong)]"
            style={{ fontSize: "var(--text-status)", fontWeight: "var(--text-status-weight)" }}
          >
            확인 필요
          </header>
          <div>
            {pending.map((link) => (
              <TopicLinkRow key={link.id} link={link} onDecide={decide} />
            ))}
          </div>
        </section>
      )}

      {asserted.length > 0 && (
        <section aria-label="연결된 회의">
          <header
            className="text-[var(--color-ink-strong)]"
            style={{ fontSize: "var(--text-status)", fontWeight: "var(--text-status-weight)" }}
          >
            연결된 회의
          </header>
          <div>
            {asserted.map((link) => (
              <TopicLinkRow key={link.id} link={link} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
