import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Autune",
  description: "회의 녹음 하나로 액션아이템 추적과 갭 탐지까지",
};

/**
 * The app shell. Sidebar 200 on paper, content panel, top bar 56 with a
 * hairline only — see docs/design/ui-spec.md section 0.
 *
 * User-facing copy is Korean; code and comments are English.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // `suppressHydrationWarning` on both: browser extensions (password
    // managers, grammar checkers) add attributes to <html> and <body> before
    // React hydrates, and React reports that as a hydration mismatch even
    // though nothing this app renders differs. It suppresses the warning for
    // these two elements' own attributes only -- a real mismatch inside the
    // tree is still reported.
    <html lang="ko" suppressHydrationWarning>
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
