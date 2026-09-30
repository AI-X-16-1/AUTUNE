import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Autune",
  description: "회의 녹음 하나로 액션아이템 추적과 갭 탐지까지",
};

/**
 * The document, and nothing else.
 *
 * The chrome moved down to `(app)/layout.tsx`, because there is one screen
 * that must not wear it: `/login` is where somebody stands before they are
 * anybody, and the top bar's wordmark links to `/`, which they cannot open
 * yet. It also drew its own header already, so signing in meant looking at
 * two — the seam between #423 and #425, neither of which existed when the
 * other was written.
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
