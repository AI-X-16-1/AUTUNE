"use client";

import { useState } from "react";

import { Button } from "@/shared/ui";

import { fileRefusal, mayHaveStored, megabytes, uploadRefusal } from "../materialUpload";
import type { MaterialUploadRules } from "../types";

/**
 * A title and a file for the team's shelf (#817). Shown only where the
 * deployment takes uploads (`rules.enabled`).
 *
 * **It says what happens to the file before the file is sent**, because the
 * three things a member would assume are all untrue: the original is not
 * kept, the text is not kept as written, and it is not kept for good. The
 * limits are the server's own numbers, not a copy of them.
 *
 * **One request, and the form waits for it.** The server reads, masks and
 * stores in the call that carries the file, so there is no progress to show
 * and nothing to ask about afterwards: the button is busy until the answer,
 * and a second file cannot be sent over the first.
 *
 * A file the rules already refuse -- by its name's ending or its size -- is
 * said so when it is chosen and is never sent.
 */
export function MaterialUploadForm({
  rules,
  onUpload,
  onUnanswered,
}: {
  rules: MaterialUploadRules;
  onUpload: (draft: { title: string; file: File }) => Promise<void>;
  /** Told when a failed upload may have stored a row all the same. */
  onUnanswered: () => void;
}) {
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  // A file input cannot be emptied by value; a new key gives a new one.
  const [round, setRound] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  const input = { ...meta, borderRadius: "var(--radius)", padding: "4px 8px" } as const;
  const refusedHere = file ? fileRefusal(file, rules) : null;

  const submit = async () => {
    // The button is disabled in each of these cases; Enter in the title is not.
    if (busy || !title.trim() || file === null || refusedHere !== null) return;
    setError(null);
    setBusy(true);
    try {
      await onUpload({ title: title.trim(), file });
      setTitle("");
      setFile(null);
      setRound((n) => n + 1);
    } catch (cause) {
      setError(uploadRefusal(cause, rules));
      if (mayHaveStored(cause)) onUnanswered();
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      aria-label="파일 올리기"
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <input
          aria-label="올릴 자료 제목"
          placeholder="제목"
          value={title}
          maxLength={rules.max_title_chars}
          onChange={(event) => setTitle(event.target.value)}
          className="w-56 border border-[var(--color-hairline)]"
          style={input}
        />
        <input
          key={round}
          type="file"
          aria-label="올릴 파일"
          accept={rules.suffixes.join(",")}
          disabled={busy}
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setError(null);
          }}
          className="min-w-0 flex-1"
          style={meta}
        />
        <Button
          type="submit"
          tone="secondary"
          size="compact"
          loading={busy}
          disabled={!title.trim() || file === null || refusedHere !== null}
        >
          파일 올리기
        </Button>
      </div>
      <p className="text-[var(--color-ink-muted)]" style={meta}>
        올린 파일의 원본은 보관하지 않으며 Autune에서 다시 열 수 없습니다. 보관하는
        것은 전화번호처럼 형식이 정해진 개인정보를 가린 글이고, 목록에 적힌 날에
        삭제됩니다. 문장 속의 이름은 가려지지 않으며, 그림으로만 들어 있는 글은
        읽지 않습니다. 한 파일 {megabytes(rules.max_bytes)}까지 · {rules.suffixes.join(" ")}
      </p>
      {busy ? (
        <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>
          파일을 읽고 개인정보를 가리는 중입니다. 몇 초 걸릴 수 있습니다.
        </span>
      ) : null}
      {refusedHere ?? error ? (
        <span role="alert" className="text-[var(--color-signal-critical)]" style={meta}>
          {refusedHere ?? error}
        </span>
      ) : null}
    </form>
  );
}
