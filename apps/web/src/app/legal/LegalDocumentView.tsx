import type { ReactNode } from "react";

import type { Block, Inline, LegalDocument, Line } from "./content";

/**
 * How a legal document's data is drawn: the preamble when it has one, then
 * each article under the chapter it opens. Shared by `/legal`, which shows the
 * documents one after another, and the consent page, which opens one at a time
 * -- so what a person reads before agreeing is the same text, drawn the same
 * way, as what the footer links to.
 *
 * Nothing here knows what the text says. An unsettled value is marked where it
 * stands, on both pages.
 *
 * **The text cannot be selected** (`select-none` on `DocumentBody`; the user,
 * 2026-10-02: every document). It is a setting of the page, not a protection:
 * the text is in the page's source, a screen reader reads it as before, and
 * printing is not affected. A customer is owed a copy of the terms on request,
 * so both pages say how to get one (`COPY_NOTICE`) -- keep that line while
 * this is on.
 */

export const BODY = {
  fontSize: "var(--text-body)",
  lineHeight: "var(--text-body-leading)",
} as const;

function isBlank(part: Inline): part is { blank: string; proposed?: string } {
  return typeof part === "object" && "blank" in part;
}

/** One unsettled value: the proposal when there is one, else what is missing. */
function Unsettled({ label, proposed }: { label: string; proposed?: string }) {
  return (
    <span className="text-attention" title={`${label}: 확정 전`}>
      〔{proposed ?? label} · 확정 전〕
    </span>
  );
}

export function Text({ line }: { line: Line }) {
  const parts: readonly Inline[] = Array.isArray(line)
    ? line
    : [line as Inline];
  return (
    <>
      {parts.map((part, index) => {
        if (typeof part === "string") return <span key={index}>{part}</span>;
        if (isBlank(part))
          return (
            <Unsettled
              key={index}
              label={part.blank}
              proposed={part.proposed}
            />
          );
        return (
          <strong
            key={index}
            className="text-ink-strong"
            style={{ fontWeight: "var(--text-label-weight)" }}
          >
            {part.strong}
          </strong>
        );
      })}
    </>
  );
}

function BlockView({ block }: { block: Block }): ReactNode {
  if (block.kind === "paragraph") {
    return (
      <p className="text-ink-body" style={BODY}>
        <Text line={block.line} />
      </p>
    );
  }
  if (block.kind === "list") {
    const List = block.ordered ? "ol" : "ul";
    return (
      <List
        className={`flex flex-col gap-2 pl-5 text-ink-body ${block.ordered ? "list-decimal" : "list-disc"}`}
        style={BODY}
      >
        {block.items.map((item, index) => (
          <li key={index}>
            <Text line={item} />
          </li>
        ))}
      </List>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table
        className="w-full border-collapse text-left"
        style={{ fontSize: "var(--text-meta)" }}
      >
        <thead>
          <tr>
            {block.head.map((cell) => (
              <th
                key={cell}
                scope="col"
                className="border-b border-hairline px-3 py-2 text-ink-muted"
                style={{ fontWeight: "var(--text-label-weight)" }}
              >
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  className={`border-b border-hairline px-3 py-2 align-top ${
                    cellIndex === 0 ? "text-ink-strong" : "text-ink-body"
                  }`}
                  style={{ lineHeight: "var(--text-meta-leading)" }}
                >
                  <Text line={cell} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Everything of a document under its title. The title is the caller's. */
export function DocumentBody({ doc }: { doc: LegalDocument }) {
  const chaptered = doc.sections.some((s) => s.chapter);
  return (
    <div className="flex select-none flex-col gap-6">
      {doc.lead ? (
        <p className="text-ink-body" style={BODY}>
          <Text line={doc.lead} />
        </p>
      ) : null}
      {doc.sections.map((section) => (
        <section key={section.heading} className="flex flex-col gap-3">
          {section.chapter ? (
            <h2
              className="mt-4 text-ink-strong"
              style={{
                fontSize: "var(--text-heading)",
                fontWeight: "var(--text-heading-weight)",
                lineHeight: "var(--text-heading-leading)",
              }}
            >
              {section.chapter}
            </h2>
          ) : null}
          <ArticleHeading chaptered={chaptered}>
            {section.heading}
          </ArticleHeading>
          {section.blocks.map((block, index) => (
            <BlockView key={index} block={block} />
          ))}
        </section>
      ))}
    </div>
  );
}

/**
 * An article's title. Under a document split into chapters (장) it is an
 * `h3` below the chapter's `h2`, so a screen reader's outline has the same
 * levels the text does; elsewhere it is the `h2`.
 */
function ArticleHeading({ chaptered, children }: { chaptered: boolean; children: string }) {
  const Tag = chaptered ? "h3" : "h2";
  return (
    <Tag
      className="text-ink-strong"
      style={{
        fontSize: "var(--text-rowTitle)",
        fontWeight: "var(--text-rowTitle-weight)",
        lineHeight: "var(--text-rowTitle-leading)",
      }}
    >
      {children}
    </Tag>
  );
}
