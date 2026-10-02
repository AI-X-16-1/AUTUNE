import type { ReactNode } from "react";

import { TabLinks } from "@/shared/ui";

/**
 * The settings screens, one tab each. Assembly only: which screens exist and
 * where they go.
 */
const TABS = [
  { href: "/settings/privacy", label: "개인정보 · 보관" },
  { href: "/settings/integrations", label: "연동" },
  { href: "/settings/approvers", label: "승인자" },
] as const;

export default function SettingsLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <div style={{ padding: "var(--space-24) var(--space-page) 0" }}>
        <div className="max-w-[760px]">
          <TabLinks tabs={TABS} label="설정" />
        </div>
      </div>
      {children}
    </>
  );
}
