import type { Metadata } from "next";

import { SignInCard } from "./SignInCard";

/**
 * S01 · Landing / sign-in — the first screen for a signed-out visitor.
 * See docs/design/ui-spec.md (S01). No team or org branding; the footer carries
 * legal links only. On success the backend redirects to the app (S05 when a
 * workspace exists, S02 when it does not).
 */
export const metadata: Metadata = {
  title: "로그인 · Autune",
  description: "회의 녹음 하나로 액션아이템 추적과 갭 탐지까지",
};

const HIGHLIGHTS = [
  ["0분", "회의 후 서기 시간"],
  ["5종 분류", "약속 · 결정 · 질문 · 우려 · 모호"],
  ["원본 즉시 삭제", "개인정보 자동 마스킹"],
] as const;

export default function LoginPage() {
  return (
    <div className="flex min-h-screen flex-col bg-paper">
      <header
        className="flex items-center border-b border-hairline"
        style={{ height: "var(--space-topbar)", paddingInline: "var(--space-page)" }}
      >
        <span
          className="text-ink-strong"
          style={{ fontSize: "var(--text-heading)", fontWeight: "var(--text-heading-weight)" }}
        >
          AUTUNE
        </span>
      </header>

      <main className="flex flex-1 items-center justify-center" style={{ padding: "var(--space-page)" }}>
        <div className="grid w-full max-w-[960px] items-center gap-x-16 gap-y-10 lg:grid-cols-2">
          <section>
            <p
              className="text-ink-muted"
              style={{ fontSize: "var(--text-label)", fontWeight: "var(--text-label-weight)" }}
            >
              회의 인텔리전스
            </p>
            <h1
              className="mt-3 text-ink-strong"
              style={{
                fontSize: "var(--text-display)",
                fontWeight: "var(--text-display-weight)",
                lineHeight: "var(--text-display-leading)",
                letterSpacing: "var(--text-display-tracking)",
              }}
            >
              회의는 끝났는데
              <br />
              실행은 시작되지 않았다면
            </h1>
            <p
              className="mt-4 max-w-[420px] text-ink-body"
              style={{ fontSize: "var(--text-body)", lineHeight: "var(--text-body-leading)" }}
            >
              녹음 하나로 누가 무엇을 언제까지 하기로 했는지 추적하고, 합의된 줄 알았지만
              정해지지 않은 항목을 회의 직후 짚어드립니다.
            </p>

            <dl className="mt-8 flex flex-col gap-3 border-t border-hairline pt-6">
              {HIGHLIGHTS.map(([term, detail]) => (
                <div key={term} className="flex items-baseline gap-3">
                  <dt
                    className="shrink-0 text-ink-strong"
                    style={{ fontSize: "var(--text-data)", fontWeight: "var(--text-data-weight)" }}
                  >
                    {term}
                  </dt>
                  <dd className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
                    {detail}
                  </dd>
                </div>
              ))}
            </dl>
          </section>

          <div className="flex justify-center lg:justify-end">
            <SignInCard />
          </div>
        </div>
      </main>

      <footer
        className="flex flex-col gap-1 border-t border-hairline text-ink-muted"
        style={{
          paddingBlock: "var(--space-16)",
          paddingInline: "var(--space-page)",
          fontSize: "var(--text-metaSmall)",
        }}
      >
        <span>Slack · Notion · Jira · Google Calendar 연동</span>
        <span>보안 · 개인정보 처리방침 · 이용약관</span>
      </footer>
    </div>
  );
}
