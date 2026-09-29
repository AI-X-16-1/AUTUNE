import type { Metadata } from "next";

import { AppHeader } from "./_components/AppHeader";
import "./globals.css";

export const metadata: Metadata = {
  title: "Autune",
  description: "회의 녹음 하나로 액션아이템 추적과 갭 탐지까지",
};

/**
 * The app shell: top bar 56 with a hairline only — see docs/design/ui-spec.md
 * section 0. The sidebar the spec also describes is not here; it would list
 * settings screens that do not exist (S28-S30), and a nav of dead links is
 * worse than no nav.
 *
 * It imposes no width on the page below it. Every screen already declares its
 * own reading width — 720 for a transcript, 1200 for the action board — and a
 * container here would either fight them or force them all to one number.
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
      <body suppressHydrationWarning>
        <AppHeader />
        {children}
      </body>
    </html>
  );
}
