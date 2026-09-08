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
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
