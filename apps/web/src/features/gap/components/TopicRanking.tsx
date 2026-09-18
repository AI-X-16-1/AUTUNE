import { MaskedText } from "@/shared/ui";

import type { Topic } from "../types";

/**
 * What carried the meeting: topics most central first, with the PageRank the
 * server normalised so the top topic is 1.
 *
 * The bar is greyscale, from the chart ramp. Signal colours appear on numeric
 * text and never as a chart fill (ui-spec section 4) — a red bar here would
 * read as "this topic is a problem", and centrality says nothing of the kind.
 *
 * **No participation.** The report carries who spoke and who was silent per
 * topic, and S20 renders that as topic × role density, never per person — so
 * it waits for `participants.role`, which nothing fills until identification
 * (#6). Rendering the ids we do have would be exactly the per-person surface
 * `docs/architecture/privacy.md` section 3 refuses.
 */
export function TopicRanking({ topics }: { topics: readonly Topic[] }) {
  if (topics.length === 0) {
    return (
      <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
        이 회의에서 추출된 토픽이 없습니다.
      </p>
    );
  }

  return (
    <ul className="flex flex-col">
      {topics.map((topic) => (
        <li
          key={topic.id}
          className="flex items-center gap-3 border-b border-[var(--color-hairline)]"
          style={{ paddingBlock: "var(--space-row)" }}
        >
          <span
            className="min-w-0 flex-1 truncate text-[var(--color-ink-strong)]"
            style={{ fontSize: "var(--text-rowTitle)" }}
          >
            <MaskedText>{topic.label}</MaskedText>
          </span>

          <span
            aria-hidden
            className="inline-block w-24 rounded-full"
            style={{
              height: "var(--bar-thickness)",
              background: "var(--color-chart-step1)",
            }}
          >
            <span
              className="block h-full rounded-full"
              style={{
                width: `${Math.round(topic.centrality * 100)}%`,
                background: "var(--color-chart-step4)",
              }}
            />
          </span>

          <span
            className="text-[var(--color-ink-body)]"
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "var(--text-data)",
              fontWeight: "var(--text-data-weight)",
            }}
          >
            {topic.centrality.toFixed(2)}
          </span>
        </li>
      ))}
    </ul>
  );
}
