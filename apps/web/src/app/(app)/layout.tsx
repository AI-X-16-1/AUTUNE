import { AppSidebar } from "../_components/AppSidebar";
import { AppTopBar } from "../_components/AppTopBar";
import { AssistantMount } from "../_components/AssistantMount";
import { SessionGate } from "../_components/SessionGate";

/**
 * The app shell every design file is drawn in: a 200px paper sidebar beside a
 * white content panel, and the panel's 56px top bar with a hairline only —
 * ui-spec.md section 0.
 *
 * It replaces a top bar that held the wordmark and nothing else, over content
 * centred at 1200px. That frame appears in no design file, so every screen
 * inside it looked unlike its mockup before a single component was compared.
 * The sidebar lists the sections that have no screen yet as well, muted and
 * unlinked; see AppSidebar.
 *
 * It wraps every screen a signed-in person sees, with two exceptions that sit
 * outside this group on purpose. `/login` is where somebody stands before they
 * are anybody, and it brings its own header. The live transcript (S13) is
 * drawn without the sidebar in its design file and lives under `(focus)`. The group changes no URL —
 * `(app)` is a folder name Next.js does not route on, the same device
 * `(review)` uses one level down.
 *
 * The panel imposes no width on the page below it. The design files lay
 * content out from the panel's left edge at the page gutter; each screen keeps
 * its own reading width inside that.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionGate>
      <div
        className="grid min-h-screen bg-[var(--color-surface-paper)]"
        style={{ gridTemplateColumns: "var(--space-sidebar) minmax(0, 1fr)" }}
      >
        <AppSidebar />
        <div className="flex min-w-0 flex-col bg-[var(--color-surface-panel)]">
          <AppTopBar />
          <div className="min-w-0 flex-1">{children}</div>
        </div>
      </div>
      {/* S34: the assistant floats over every screen in this group, and only these. */}
      <AssistantMount />
    </SessionGate>
  );
}
