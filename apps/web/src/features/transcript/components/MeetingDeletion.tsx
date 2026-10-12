"use client";

import { useState, type CSSProperties, type FormEvent } from "react";

import { ApiError } from "@/shared/api/client";
import { Button } from "@/shared/ui";

import { deleteMeeting } from "../api";
import { MAX_TITLE_CHARS } from "./MeetingTitle";

const META: CSSProperties = { fontSize: "var(--text-meta)" };
const INPUT =
  "rounded-[var(--radius)] bg-[var(--color-surface-panel)] px-3 text-[var(--color-ink-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus)]";
const INPUT_STYLE: CSSProperties = {
  height: "var(--control-h-default)",
  fontSize: "var(--text-rowBody)",
  border: "1px solid var(--color-hairline)",
};

/** What the server said, as the person can act on it. */
const REFUSED: Record<string, string> = {
  meeting_title_mismatch:
    "입력한 이름이 회의 이름과 다릅니다. 띄어쓰기와 대소문자까지 그대로 입력해 주세요.",
  meeting_in_progress:
    "전사 중이거나 실시간으로 진행 중인 회의는 삭제할 수 없습니다. 끝나거나 취소한 뒤에 다시 시도해 주세요.",
  permission_denied: "이 회의를 삭제할 수 없습니다.",
  not_found: "회의를 찾을 수 없습니다. 이미 삭제되었을 수 있습니다.",
};

/**
 * The way to delete a meeting, on the meeting's own screen (#1161).
 *
 * Any member of the meeting's team may; the server decides. Before anything
 * is sent the screen says what goes, what Autune asks the team's tools to
 * take back, and what stays in them -- the same list the legal notice gives
 * -- and the meeting's title is typed, as a team's name is typed to delete
 * the team. The server compares it; the button only waits for something to
 * have been typed, so there is one rule for what counts as the same title.
 *
 * Afterwards the person is taken to the meeting list, by a full load: every
 * list on the way still held the meeting.
 */
export function MeetingDeletion({ meetingId }: { meetingId: string }) {
  const [typed, setTyped] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  if (typed === null) {
    return (
      <Button tone="destructiveText" size="compact" type="button" onClick={() => setTyped("")}>
        회의 삭제
      </Button>
    );
  }

  async function remove(event: FormEvent) {
    event.preventDefault();
    if (busy || !typed?.trim()) return;
    setNote(null);
    setBusy(true);
    try {
      await deleteMeeting(meetingId, typed);
      window.location.assign("/");
    } catch (cause: unknown) {
      const code = cause instanceof ApiError ? cause.code : "";
      // Not "nothing was deleted": a request that did not come back may have
      // been carried out.
      setNote(REFUSED[code] ?? "회의를 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.");
      setBusy(false);
    }
  }

  return (
    <form aria-label="회의 삭제" className="mt-3 max-w-[560px]" onSubmit={remove}>
      <p style={META}>
        삭제하면 이 회의와 전사, 할 일, 결정 사항, 갭 리포트, 리포트를 비롯해 Autune이 이 회의에
        대해 보관하는 것이 모두 삭제됩니다. 다른 사람이 이 회의에서 한 말도 함께 삭제되며, 그
        사람들에게 알림은 가지 않습니다. 삭제한 뒤에는 되돌릴 수 없습니다.
      </p>
      <p className="mt-2" style={META}>
        이 회의의 할 일로 Google Calendar에 넣은 일정, 다음 회의 일정의 설명에 넣은 이 회의의 갭
        질문, Slack, Notion, Jira에 보낸 프로젝트별 회의록은 삭제를 요청합니다. 그 도구가
        응답하지 않으면 남을 수 있습니다.
      </p>
      <p className="mt-2" style={META}>
        팀의 도구에 이미 보낸 그 밖의 것은 삭제되지 않고 그 도구에 남습니다. 할 일·결정 사항의
        Notion 페이지와 Jira 이슈, 팀 Slack 채널에 올라간 갭 질문과 다음 회의 안내, 이전 회의
        연결 알림과 결정 변경 알림, 회의 전 브리핑, 회의 리포트와 주간 팀 리포트가 그렇습니다.
        이 회의의 후속 회의로 잡은 Google Calendar 일정은 잡은 사람의 일정으로 남습니다. 각자
        Slack 개인 메시지로 받은 알림도 남습니다. 지워야 하는 것이 있으면 그 도구에서 직접 지워
        주세요.
      </p>
      <label className="mt-3 block" style={META}>
        삭제하려면 이 회의의 이름을 입력해 주세요.
        <input
          type="text"
          value={typed}
          onChange={(event) => {
            setTyped(event.target.value);
            setNote(null);
          }}
          maxLength={MAX_TITLE_CHARS}
          autoComplete="off"
          className={`${INPUT} mt-1 block w-full max-w-[360px]`}
          style={INPUT_STYLE}
        />
      </label>
      <div className="mt-2 flex gap-2">
        <Button
          tone="primary"
          size="compact"
          type="submit"
          loading={busy}
          disabled={!typed.trim()}
        >
          이 회의 삭제
        </Button>
        <Button
          tone="quiet"
          size="compact"
          type="button"
          disabled={busy}
          onClick={() => {
            setTyped(null);
            setNote(null);
          }}
        >
          취소
        </Button>
      </div>
      {note ? (
        <p role="alert" className="mt-2" style={{ ...META, color: "var(--color-signal-attention)" }}>
          {note}
        </p>
      ) : null}
    </form>
  );
}
