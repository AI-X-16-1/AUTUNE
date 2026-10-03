import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import { Band } from "@/shared/ui";

import {
  COPY_NOTICE,
  LEGAL_DOCUMENTS,
  type Block,
  type Inline,
  type Line,
} from "./content";

/**
 * The privacy policy, the terms of service and the information security policy
 * on one page -- what S01's footer and its consent sentence point at.
 *
 * Outside the `(app)` group, like `/login`: the sentence "계속하면 이용약관과
 * 개인정보 처리방침에 동의하는 것입니다" is read by somebody who has not signed
 * in yet, so the page must open without a session and without the app's chrome.
 *
 * One route with three anchors (`#privacy`, `#terms`, `#security`) rather than
 * three routes: the three refer to each other, and a reader checking what
 * "삭제" means in the terms wants the policy's table a scroll away.
 *
 * The text is data (`./content`); nothing here knows what it says. While any
 * value in it is unsettled the page says so at the top, and marks each one
 * where it stands.
 *
 * **The documents' text cannot be selected** (`select-none`; the user,
 * 2026-10-02). It is a setting of the page, not a protection: the text is in
 * the page's source, a screen reader reads it as before, and printing is not
 * affected. A customer is owed a copy of the terms on request, so the page
 * says how to get one (`COPY_NOTICE`) -- keep that line while this is on.
 */
export const metadata: Metadata = {
  title: "개인정보 처리방침 · 서비스 이용약관 · 정보보호 정책 · Autune",
  description: "Autune의 개인정보 처리방침, 서비스 이용약관, 정보보호 정책",
};

const BODY = {
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

function Text({ line }: { line: Line }) {
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

export default function LegalPage() {
  return (
    <div className="flex min-h-screen flex-col bg-paper">
      <header
        className="flex items-center border-b border-hairline"
        style={{
          height: "var(--space-topbar)",
          paddingInline: "var(--space-page)",
        }}
      >
        <Link
          href="/login"
          className="text-ink-strong"
          style={{
            fontSize: "var(--text-heading)",
            fontWeight: "var(--text-heading-weight)",
          }}
        >
          AUTUNE
        </Link>
      </header>

      <main
        className="mx-auto flex w-full max-w-[840px] flex-col gap-10"
        style={{ padding: "var(--space-page)" }}
      >
        <Band>
          초안입니다. 법률 검토를 거치지 않았고, 〔 〕로 표시한 항목은 아직
          확정되지 않았습니다.
        </Band>

        <nav
          aria-label="문서 목차"
          className="flex flex-wrap gap-x-4 gap-y-1 text-ink-muted"
          style={BODY}
        >
          {LEGAL_DOCUMENTS.map((doc) => (
            <a
              key={doc.id}
              href={`#${doc.id}`}
              className="underline underline-offset-4"
            >
              {doc.title}
            </a>
          ))}
        </nav>

        <p className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
          <Text line={COPY_NOTICE} />
        </p>

        {LEGAL_DOCUMENTS.map((doc) => (
          <article
            key={doc.id}
            id={doc.id}
            className="flex scroll-mt-6 select-none flex-col gap-6"
          >
            <h1
              className="text-ink-strong"
              style={{
                fontSize: "var(--text-heading)",
                fontWeight: "var(--text-heading-weight)",
                lineHeight: "var(--text-heading-leading)",
              }}
            >
              {doc.title}
            </h1>
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
                <ArticleHeading chaptered={doc.sections.some((s) => s.chapter)}>
                  {section.heading}
                </ArticleHeading>
                {section.blocks.map((block, index) => (
                  <BlockView key={index} block={block} />
                ))}
              </section>
            ))}
          </article>
        ))}
      </main>
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
