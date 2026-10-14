"use client";

import { BriefPanel } from "./BriefPanel";
import { TopicLinkRow } from "./TopicLinkRow";
import { useBrief } from "../hooks/useBrief";
import { useCarriedAgenda, type CarriedSource } from "../hooks/useCarriedAgenda";
import { useTopicLinks } from "../hooks/useTopicLinks";

/**
 * S15's "context" tab: the pre-meeting brief when the meeting has one, then
 * this meeting's topic links to past meetings.
 *
 * The brief draws nothing for a meeting without one, so a finished meeting's
 * tab reads exactly as it did before briefs existed.
 *
 * `agendaSources` is what other modules have for the brief's agenda draft
 * (#1147); the route supplies it, and with none the brief is drawn as before.
 */
export function ContextTab({
  meetingId,
  agendaSources,
}: {
  meetingId: string;
  agendaSources?: readonly CarriedSource[];
}) {
  const { brief, error } = useBrief(meetingId);
  const carried = useCarriedAgenda(brief, agendaSources);
  return (
    <div style={{ display: "grid", gap: "var(--space-page)" }}>
      <BriefPanel
        brief={brief}
        error={error}
        carried={agendaSources === undefined ? undefined : carried.groups}
        carriedWaiting={carried.waiting}
      />
      <TopicLinks meetingId={meetingId} quietWhenEmpty={brief !== null} />
    </div>
  );
}

/**
 * This meeting's topic links to past meetings.
 *
 * Pending links need a decision before they count as a link at all — E never
 * sees one until it is `asserted` or `confirmed` (`_PUBLISHABLE` in
 * `service.py`) — so they sit in their own section rather than mixed in,
 * ochre and un-decided rather than settled.
 */
function TopicLinks({
  meetingId,
  quietWhenEmpty,
}: {
  meetingId: string;
  /** A meeting that has a brief has not happened yet; "no linked meetings" says nothing there. */
  quietWhenEmpty: boolean;
}) {
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
    if (quietWhenEmpty) return null;
    // Say how a link comes about, so the tab explains itself when it has
    // nothing to show (#1178). There is no way to add a link by hand.
    return (
      <div style={{ fontSize: "var(--text-rowBody)" }}>
        <p className="text-[var(--color-ink-strong)]">이 회의와 연결된 과거 회의가 없습니다.</p>
        <p className="mt-2 text-[var(--color-ink-muted)]">
          같은 팀의 이전 회의에서 다룬 주제가 이 회의에 다시 나오면, 그 회의와 자동으로 연결됩니다.
          확실하지 않은 연결은 &lsquo;확인 필요&rsquo;로 여기에 올라오고, 맞는지 직접 고를 수
          있습니다.
        </p>
      </div>
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
