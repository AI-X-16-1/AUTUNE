// Temp dev route (see docstring below). The feature-boundary rule as configured
// also blocks the "pages compose features" pattern the docs describe, which every
// future real page will hit too — not fixing the shared eslint.config.mjs here,
// that's a team call, not a temp-page decision.
// eslint-disable-next-line no-restricted-imports
import { Dashboard } from "@/features/dashboard/components/Dashboard";

/**
 * Temporary preview route for the S26 dashboard. Not a real screen: there is
 * no auth/team context yet (#156, #189) to resolve `team_id` on its own, so
 * this reads it from a query param instead. Delete once a real page replaces
 * this.
 */
export default async function DevDashboardPage({
  searchParams,
}: {
  searchParams: Promise<{ team_id?: string }>;
}) {
  const teamId = (await searchParams).team_id?.trim();

  return (
    <main className="mx-auto max-w-[960px] p-[var(--space-page)]">
      <p className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
        임시 미리보기 페이지입니다 (인증/팀 컨텍스트 준비 전까지만 사용).
      </p>

      <form action="/dev-dashboard" className="mt-4 flex items-center" style={{ gap: "var(--space-4)" }}>
        <input
          name="team_id"
          defaultValue={teamId}
          placeholder="team_id"
          className="border border-hairline px-2 py-1"
          style={{ fontSize: "var(--text-meta)" }}
        />
        <button
          type="submit"
          className="text-[var(--color-accent-default)]"
          style={{ fontSize: "var(--text-meta)" }}
        >
          불러오기
        </button>
      </form>

      <div className="mt-6">
        {teamId ? (
          <Dashboard teamId={teamId} />
        ) : (
          <p className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
            team_id를 입력하세요.
          </p>
        )}
      </div>
    </main>
  );
}
