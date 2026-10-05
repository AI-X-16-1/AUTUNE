import { notFound } from "next/navigation";

import { DrivePreview } from "@/shared/drive/DrivePreview";

/**
 * A place to try the Drive preview before any screen holds one (the user,
 * 2026-10-05). Not a real screen: no material is registered anywhere yet
 * (#817 is still open), so this takes a Drive link from the query string and
 * shows Google's own preview of it.
 *
 * Outside the signed-in shell on purpose: the preview is Google's page under
 * the viewer's own Google sign-in, and asks Autune for nothing -- so it can be
 * tried with only the web server running.
 *
 * `notFound()` in production, as the other `dev-` routes do, so "temporary"
 * is a fact and not a promise in a comment.
 */
export default async function DevDrivePreviewPage({
  searchParams,
}: {
  searchParams: Promise<{ link?: string }>;
}) {
  if (process.env.NODE_ENV === "production") {
    notFound();
  }

  const link = (await searchParams).link?.trim() ?? "";

  return (
    <main className="mx-auto max-w-[960px] p-[var(--space-page)]">
      <p className="text-ink-muted" style={{ fontSize: "var(--text-meta)" }}>
        Drive 자료 미리보기를 시험하는 임시 페이지입니다. Drive에서 &lsquo;링크
        복사&rsquo;로 받은 주소를 넣어 주세요.
      </p>

      <form
        action="/dev-drive-preview"
        className="mt-4 flex items-center"
        style={{ gap: "var(--space-4)" }}
      >
        <input
          name="link"
          aria-label="Drive 링크"
          defaultValue={link}
          placeholder="https://drive.google.com/file/d/…"
          className="min-w-0 flex-1 border border-hairline px-2 py-1"
          style={{ fontSize: "var(--text-meta)" }}
        />
        <button
          type="submit"
          className="text-[var(--color-accent-default)]"
          style={{ fontSize: "var(--text-meta)" }}
        >
          미리보기
        </button>
      </form>

      <div className="mt-6">
        {link !== "" ? (
          <DrivePreview link={link} />
        ) : (
          <p
            className="text-ink-muted"
            style={{ fontSize: "var(--text-meta)" }}
          >
            링크를 넣으면 여기에 미리보기가 보입니다.
          </p>
        )}
      </div>
    </main>
  );
}
