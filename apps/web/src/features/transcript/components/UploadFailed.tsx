import { Button } from "@/shared/ui/Button";

/**
 * S13 after the recording could not be uploaded.
 *
 * The audio exists only in this tab's memory: the live channel keeps none
 * and the server never received the file. So the screen offers both ways to
 * keep it -- try the upload again, or save the file to this computer and
 * upload it to the same meeting later (S03 takes `.webm`).
 */
export function UploadFailed({
  error,
  onRetry,
  onSave,
}: {
  error: string | null;
  onRetry: () => void;
  onSave: () => void;
}) {
  return (
    <>
      <p
        role="alert"
        style={{
          fontSize: "var(--text-meta)",
          color: "var(--color-signal-attention)",
        }}
      >
        {error} 녹음은 이 탭에만 있어 탭을 닫으면 사라집니다. 다시 올리거나
        파일로 저장해 두세요.
      </p>
      <div className="mt-3 flex gap-2">
        <Button tone="primary" onClick={onRetry}>
          다시 올리기
        </Button>
        <Button tone="secondary" onClick={onSave}>
          파일로 저장
        </Button>
      </div>
      {/* Autune cannot reach a file on this computer, and it holds the other
          attendees' voices: the person who saved it is the one who can delete
          it (privacy.md section 1). */}
      <p
        className="mt-2 text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-meta)" }}
      >
        저장한 파일은 Autune에서 지울 수 없으니 회의에 올린 뒤 직접 지워 주세요.
      </p>
    </>
  );
}
