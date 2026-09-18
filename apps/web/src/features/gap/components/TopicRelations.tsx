import { MaskedText } from "@/shared/ui";

import { CO_OCCURS, RELATION_LABELS, relationLines } from "../types";
import type { RelationLine, TopicGraph } from "../types";

/**
 * How the meeting's topics stand to each other.
 *
 * **Asserted relations lead; co-occurrence sits below a rule.** The two are not
 * the same kind of claim and ranking them together would say they were. An
 * asserted relation is one a rule read off a marker the speaker actually said —
 * "실시간은 콜드스타트가 안 잡혀 있어서 무리입니다" is the meeting stating that
 * one topic blocks another. Co-occurrence is proximity: the graph writes it
 * without asking the extractor, and two topics in one breath may have nothing
 * to do with each other. `docs/modules/gap.md` draws the same line.
 *
 * The asserted section is empty for most meetings today, and that is the honest
 * state rather than a broken one — the rules assert one relation over the
 * typical fixture against six co-occurrence edges. The empty copy says which
 * question it is answering so an empty list does not read as "these topics are
 * unrelated".
 *
 * **No participation, no per-person anything.** Relations are topic-to-topic.
 * Who spoke on a topic is in the report and renders as role density, never per
 * person — `docs/architecture/privacy.md` section 3.
 */
export function TopicRelations({ graph }: { graph: TopicGraph | null }) {
  const lines = relationLines(graph);
  const asserted = lines.filter((line) => line.relation !== CO_OCCURS);
  const proximity = lines.filter((line) => line.relation === CO_OCCURS);

  if (lines.length === 0) {
    return (
      <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
        이 회의에서 토픽 간 관계가 확인되지 않았습니다.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col">
        {asserted.length > 0 ? (
          asserted.map((line) => <RelationRow key={line.key} line={line} />)
        ) : (
          <p
            className="text-[var(--color-ink-muted)]"
            style={{ fontSize: "var(--text-metaSmall)" }}
          >
            발화에서 읽어낸 관계가 아직 없습니다. 아래는 함께 언급된 토픽입니다.
          </p>
        )}
      </section>

      {proximity.length > 0 ? (
        <section>
          <h2
            className="mb-1 text-[var(--color-ink-muted)]"
            style={{ fontSize: "var(--text-metaSmall)" }}
          >
            {RELATION_LABELS[CO_OCCURS]}
          </h2>
          {proximity.map((line) => (
            <RelationRow key={line.key} line={line} />
          ))}
        </section>
      ) : null}
    </div>
  );
}

/**
 * One relation as a line: source, what joins it, target, weight.
 *
 * The arrow is `aria-hidden` and the relation word carries the meaning, so a
 * screen reader hears "정렬 로직 의존 인덱스" rather than a glyph. A mutual pair
 * is marked on the relation word instead of by drawing a second row, which is
 * what the payload would otherwise produce.
 */
function RelationRow({ line }: { line: RelationLine }) {
  const label = RELATION_LABELS[line.relation] ?? line.relation;

  return (
    <div
      className="flex items-center gap-3 border-b border-[var(--color-hairline)]"
      style={{ paddingBlock: "var(--space-row)" }}
    >
      <span
        className="min-w-0 flex-1 truncate text-right text-[var(--color-ink-strong)]"
        style={{ fontSize: "var(--text-rowTitle)" }}
      >
        <MaskedText>{line.sourceLabel}</MaskedText>
      </span>

      <span
        className="shrink-0 whitespace-nowrap text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        {label}
        <span aria-hidden>{line.mutual ? " ↔ " : " → "}</span>
      </span>

      <span
        className="min-w-0 flex-1 truncate text-[var(--color-ink-strong)]"
        style={{ fontSize: "var(--text-rowTitle)" }}
      >
        <MaskedText>{line.targetLabel}</MaskedText>
      </span>

      <span
        className="shrink-0 text-[var(--color-ink-body)]"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "var(--text-data)",
          fontWeight: "var(--text-data-weight)",
        }}
      >
        {line.weight.toFixed(2)}
      </span>
    </div>
  );
}
