import { driveOpenUrl, drivePreviewUrl, parseDriveLink } from "./driveLink";

/**
 * A Drive file shown where it is talked about, by Google's own preview in a
 * frame (the user, 2026-10-05: "미리보기 임베딩", the first of two ways).
 *
 * **Autune reads nothing here and stores nothing.** The frame is Google's
 * page, loaded by the viewer's browser under the viewer's own Google sign-in:
 * whether they may see the file is Google's answer, not ours, and no byte of
 * the file passes through Autune. So it needs no Drive permission, and it
 * shows a file to nobody who could not open it in Drive already.
 *
 * **What that costs.** It only works in a browser that is signed in to a
 * Google account allowed to see the file, and a browser that blocks
 * third-party cookies may show Google's sign-in page -- or nothing -- instead.
 * This component cannot tell: a frame from another origin does not say what
 * it drew. So the way out is always on screen: the note says why it may be
 * empty, and the link opens the file in Drive. It also cannot turn to a page
 * or mark a passage; that is what the second way (reading the file with the
 * viewer's own grant into a viewer of ours) is for.
 *
 * **The frame can only be Google's preview of that file.** Its address is
 * built from the file's id (`driveLink.ts`), never from what was pasted; it is
 * sandboxed to what the preview needs; and it is sent no referrer, so Google
 * is not told which Autune page was showing the file.
 */
export function DrivePreview({
  link,
  title,
}: {
  link: string;
  title?: string;
}) {
  const file = parseDriveLink(link);
  const meta = { fontSize: "var(--text-metaSmall)" } as const;

  if (file === null) {
    return (
      <p
        role="alert"
        className="text-[var(--color-signal-critical)]"
        style={meta}
      >
        Google Drive 파일의 링크가 아닙니다. Drive에서 &lsquo;링크 복사&rsquo;로
        받은 주소를 넣어 주세요.
      </p>
    );
  }

  return (
    <figure className="flex flex-col gap-2">
      <iframe
        src={drivePreviewUrl(file)}
        title={title ?? "Google Drive 자료 미리보기"}
        className="w-full rounded-[var(--radius)] border border-[var(--color-hairline)]"
        style={{ aspectRatio: "4 / 3", minHeight: "360px" }}
        sandbox="allow-scripts allow-same-origin allow-popups allow-forms"
        referrerPolicy="no-referrer"
        loading="lazy"
        allowFullScreen
      />
      <figcaption className="text-[var(--color-ink-muted)]" style={meta}>
        미리보기가 비어 있거나 로그인 화면이 보이면, 이 파일을 볼 수 있는 Google
        계정으로 이 브라우저에 로그인되어 있는지 확인해 주세요. 브라우저가 다른
        사이트의 쿠키를 막으면 보이지 않을 수 있습니다.{" "}
        <a
          className="text-[var(--color-accent-default)]"
          href={driveOpenUrl(file)}
          target="_blank"
          rel="noopener noreferrer"
        >
          Drive에서 열기
        </a>
      </figcaption>
    </figure>
  );
}
