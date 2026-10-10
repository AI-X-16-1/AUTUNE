import type { Metadata } from "next";
import Link from "next/link";

import { Band, Wordmark } from "@/shared/ui";

import { COPY_NOTICE, LEGAL_DOCUMENTS } from "./content";
import { BODY, DocumentBody, Text } from "./LegalDocumentView";

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
 * The text is data (`./content`) and how it is drawn is `LegalDocumentView`,
 * which the consent page uses too; nothing here knows what it says. While any
 * value in it is unsettled the page says so at the top, and each one is
 * marked where it stands.
 */
export const metadata: Metadata = {
  title: "개인정보 처리방침 · 서비스 이용약관 · 정보보호 정책 · Autune",
  description: "Autune의 개인정보 처리방침, 서비스 이용약관, 정보보호 정책",
};

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
        <Link href="/login" className="text-ink-strong">
          <Wordmark height={20} />
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
            className="flex scroll-mt-6 flex-col gap-6"
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
            <DocumentBody doc={doc} />
          </article>
        ))}
      </main>
    </div>
  );
}
