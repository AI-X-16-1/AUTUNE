import type { Metadata } from "next";

// Pretendard, self-hosted. tokens.css names it first in --font-sans, and every
// design file loads it; without this line the app fell back to the system
// Korean face and no screen matched its mockup. The dynamic-subset build splits
// the font by unicode range, so a page downloads only the glyphs it shows.
//
// The *variable* build, not the static one: static declares 828 @font-face
// rules (nine weights, each split into subsets) and `next build`'s css-loader
// overflows the stack on it, which `next dev` does not show. Variable is one
// weight axis — 92 rules — and still gives the 400/500/600/700 the design uses.
// Its family is 'Pretendard Variable', which design-tokens.json lists first.
import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Autune",
  description: "회의 녹음 하나로 할 일 추적과 갭 탐지까지",
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
 * `data-theme="light"` pins the light tokens. Dark is a user preference in
 * ui-spec.md, not something to inherit from the operating system, and there is
 * no preference control yet — so a Mac in dark mode was rendering a theme no
 * design file shows. tokens.css already guards its system-preference block with
 * `:root:not([data-theme="light"])`; this is the attribute it guards against.
 * A theme toggle replaces the constant when it exists.
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
    <html lang="ko" data-theme="light" suppressHydrationWarning>
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
