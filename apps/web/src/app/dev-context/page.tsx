import { ContextTab, DecisionLineagePanel } from "@/features/context";
import { notFound } from "next/navigation";

/**
 * Temporary preview route for the S15 context tab and S22 lineage panel.
 * Not a real screen: there is no auth/team context yet (#156, #189) to
 * resolve `meeting_id`/`team_id` on their own, so this reads them from query
 * params instead. Delete once a real page replaces this. Same pattern as
 * module E's `dev-dashboard` (#210).
 *
 * `notFound()` in production makes the "temporary" claim a fact rather than a
 * promise a stale comment makes — same reasoning as #241's default-off CORS.
 * This route renders any meeting's/team's context data for typed ids with no
 * auth check.
 */
export default async function DevContextPage({
  searchParams,
}: {
  searchParams: Promise<{ meeting_id?: string; team_id?: string }>;
}) {
  if (process.env.NODE_ENV === "production") {
    notFound();
  }

  const params = await searchParams;
  const meetingId = params.meeting_id?.trim();
  const teamId = params.team_id?.trim();

  return (
    <main className="mx-auto max-w-[960px] p-[var(--space-page)]">
      <p className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
        임시 미리보기 페이지입니다 (인증/팀 컨텍스트 준비 전까지만 사용).
      </p>

      <form action="/dev-context" className="mt-4 flex items-center" style={{ gap: "var(--space-4)" }}>
        <input
          name="meeting_id"
          defaultValue={meetingId}
          placeholder="meeting_id"
          className="border border-hairline px-2 py-1"
          style={{ fontSize: "var(--text-meta)" }}
        />
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

      <section className="mt-8">
        <header
          className="text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-heading)", fontWeight: "var(--text-heading-weight)" }}
        >
          S15 — 컨텍스트 탭
        </header>
        <div className="mt-2">
          {meetingId ? (
            <ContextTab meetingId={meetingId} />
          ) : (
            <p className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
              meeting_id를 입력하세요.
            </p>
          )}
        </div>
      </section>

      <section className="mt-10">
        <header
          className="text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-heading)", fontWeight: "var(--text-heading-weight)" }}
        >
          S22 — 결정 계보
        </header>
        <div className="mt-2">
          {teamId ? (
            <DecisionLineagePanel teamId={teamId} />
          ) : (
            <p className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
              team_id를 입력하세요.
            </p>
          )}
        </div>
      </section>
    </main>
  );
}
