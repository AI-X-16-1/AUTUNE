import { Button } from "@/shared/ui/Button";

/**
 * A read that failed, said in Korean, with a way to ask again (#1176).
 *
 * The message is the caller's (`readableError`), never the server's text. The
 * button asks for the same thing once more; it does not reload the page, so
 * whatever else the screen already shows stays.
 */
export function LoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-2">
      <span style={{ fontSize: "var(--text-meta)", color: "var(--color-signal-attention)" }}>
        {message}
      </span>
      <Button tone="text" size="compact" type="button" onClick={onRetry}>
        다시 시도
      </Button>
    </div>
  );
}
