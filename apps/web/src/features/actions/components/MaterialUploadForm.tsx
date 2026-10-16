"use client";

import { useRef, useState } from "react";

import { Button } from "@/shared/ui";

import { driveRefusal } from "../drivePicker";
import {
  fileRefusal,
  mayHaveStored,
  megabytes,
  titleFromFileName,
  uploadRefusal,
} from "../materialUpload";
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
  keepUntilDeleted = null,
  onUpload,
  onUnanswered,
  pickDrive,
  showNotes = true,
}: {
  rules: MaterialUploadRules;
  /**
   * Whether the notes under the form are shown. The page lets a person fold
   * them; they are open on every visit and the choice is not kept, so nothing
   * new is stored in the browser.
   */
  showNotes?: boolean;
  /**
   * Choose a file from Google Drive (#1369): the browser fetches it and the
   * chosen file goes through the same upload as one from disk. Absent where
   * the deployment has no Picker configuration; the button is then not drawn.
   * Resolves to null when the person closes the Picker.
   */
  pickDrive?: () => Promise<File | null>;
  /**
   * The team's choice (`MaterialKeepSetting`): true keeps uploads until a
   * member deletes them; false or unknown, each row shows the day it goes.
   */
  keepUntilDeleted?: boolean | null;
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
  // True while Google's Picker is open or its file is being fetched.
  const [picking, setPicking] = useState(false);
  // Set when `file` came from Drive: the file input then shows nothing.
  const [fromDrive, setFromDrive] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  // The title the form filled in from a file's name, so that the next file may
  // replace it and a title the person typed never is.
  const filled = useRef("");
  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  const input = { ...meta, borderRadius: "var(--radius)", padding: "4px 8px" } as const;
  const refusedHere = file ? fileRefusal(file, rules) : null;

  const suggestTitle = (chosen: File | null) => {
    if (!chosen) return;
    if (title.trim() !== "" && title !== filled.current) return;
    const suggestion = titleFromFileName(chosen.name, rules.max_title_chars);
    filled.current = suggestion;
    setTitle(suggestion);
  };

  const chooseFromDrive = async () => {
    if (!pickDrive || busy || picking) return;
    setError(null);
    setPicking(true);
    try {
      const picked = await pickDrive();
      // Closing the Picker keeps whatever was chosen before.
      if (picked) {
        setFile(picked);
        setFromDrive(true);
        suggestTitle(picked);
        setRound((n) => n + 1);
      }
    } catch (cause) {
      setError(driveRefusal(cause));
    } finally {
      setPicking(false);
    }
  };

  const submit = async () => {
    // The button is disabled in each of these cases; Enter in the title is not.
    if (busy || picking || !title.trim() || file === null || refusedHere !== null) return;
    setError(null);
    setBusy(true);
    try {
      await onUpload({ title: title.trim(), file });
      setTitle("");
      filled.current = "";
      setFile(null);
      setFromDrive(false);
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
          ref={fileInput}
          type="file"
          aria-label="올릴 파일"
          accept={rules.suffixes.join(",")}
          disabled={busy || picking}
          tabIndex={-1}
          onChange={(event) => {
            const chosen = event.target.files?.[0] ?? null;
            setFile(chosen);
            setFromDrive(false);
            setError(null);
            suggestTitle(chosen);
          }}
          className="sr-only"
        />
        <Button
          type="button"
          tone="secondary"
          size="compact"
          disabled={busy || picking}
          onClick={() => fileInput.current?.click()}
        >
          파일 선택
        </Button>
        {pickDrive ? (
          <Button
            type="button"
            tone="secondary"
            size="compact"
            loading={picking}
            disabled={busy}
            onClick={() => void chooseFromDrive()}
          >
            Drive에서 선택
          </Button>
        ) : null}
        <Button
          type="submit"
          tone="secondary"
          size="compact"
          loading={busy}
          disabled={picking || !title.trim() || file === null || refusedHere !== null}
        >
          파일 올리기
        </Button>
      </div>
      {file ? (
        <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>
          {fromDrive ? "Drive에서 고른 파일:" : "고른 파일:"} {file.name}
        </span>
      ) : null}
      {pickDrive ? (
        <p hidden={!showNotes} className="text-[var(--color-ink-muted)]" style={meta}>
          Drive에서 고른 파일은 이 브라우저가 Google에서 받아 올립니다. Autune 서버는
          Drive에 접속하지 않으며, Autune이 접근할 수 있는 것은 이번에 고른 파일뿐입니다.
          이후는 위와 같습니다.
        </p>
      ) : null}
      <p hidden={!showNotes} className="text-[var(--color-ink-muted)]" style={meta}>
        올린 파일의 원본은 보관하지 않으며 Autune에서 다시 열 수 없습니다. 보관하는
        것은 전화번호처럼 형식이 정해진 개인정보를 가린 글이고,{" "}
        {keepUntilDeleted === true
          ? "팀이 지울 때까지 보관합니다."
          : "목록에 적힌 날에 삭제됩니다."}{" "}
        문장 속의 이름은 가려지지 않으며, 그림으로만 들어 있는 글은
        읽지 않습니다. 줄이나 쪽이 바뀌는 곳에서 나뉜 번호나 전자우편 주소는 가려지지 않을 수
        있습니다. 한 파일 {megabytes(rules.max_bytes)}까지 · {rules.suffixes.join(" ")}
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
