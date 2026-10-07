"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";

import { Button, ChipToggle } from "@/shared/ui";

import { createTeam, listTeams } from "../api";
import { announceTeams, rememberTeam } from "../selectedTeam";

import { TeamInvite } from "./TeamInvite";
import { ROLES } from "./WorkspaceScreen";

/**
 * Making a team from the sidebar, and inviting its members in the same small
 * window (the user, 2026-10-06: "사이드바에 팀 옆에 + 버튼으로 팀생성하면서
 * 구성원들에게 메일을 보내거나 초대링크를 생성하게 작은 화면 띄워줘").
 *
 * It is S02 (`WorkspaceScreen`) in a window, not a second way of doing either
 * thing: the team is made by the same call with its creator alone on it, and
 * the people named afterwards are invited by the same control (`TeamInvite`)
 * -- an invitation adds nobody until the invited person accepts it, signed in
 * under the invited address (#552). The page stays: somebody on no team has
 * no sidebar menu to press and is still sent there.
 *
 * **Mail when the person has connected Gmail, a link otherwise.** That is
 * `TeamInvite`'s own rule. Connecting Gmail is not offered here: it leaves
 * the page for Google and comes back to it, and a window would not survive
 * that. It is offered under 설정 › 구성원, where the same control lives.
 *
 * **The new team becomes the team being looked at**, and every list of teams
 * on the page reads again, so the sidebar shows it at once. Ours to decide:
 * somebody who has just made a team and is inviting people to it is looking
 * at that team.
 *
 * **It opens in the middle of the screen, over a dimmed page** (the user,
 * 2026-10-07: "팀 추가버튼의 경우 액션처럼 작은 화면 띄워줘") -- the way an
 * action item's window does (`ActionDetailDrawer`), and no longer hung beside
 * the sidebar at the height of the "+". It is drawn on `document.body`, not
 * inside the sidebar: the sidebar is its own layer, and a dimming drawn in it
 * would lie under whatever the page itself raises.
 *
 * It closes on "닫기", on Escape and on a press outside it -- which is now a
 * press on the dimmed page. Before the team is made that loses only a name;
 * after, the team exists and the invitations made so far stand -- more can be
 * made under 설정 › 구성원.
 */

const INPUT =
  "w-full rounded-[var(--radius)] bg-[var(--color-surface-panel)] px-3 text-[var(--color-ink-strong)] focus-visible:outline-none";
const INPUT_STYLE = {
  height: "var(--control-h-default)",
  fontSize: "var(--text-rowBody)",
  border: "var(--border-input)",
} as const;
const LABEL = {
  fontSize: "var(--text-label)",
  fontWeight: "var(--text-label-weight)",
  color: "var(--color-ink-body)",
} as const;
const META = { fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" } as const;

export function NewTeamWindow({
  opener,
  onClose,
}: {
  /** The control that opened this: a press on it is not a press outside. */
  opener: RefObject<HTMLElement | null>;
  onClose: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ teamId: string; name: string } | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    const onPress = (event: MouseEvent) => {
      const target = event.target as Node;
      if (box.current?.contains(target) || opener.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPress);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPress);
    };
  }, [onClose, opener]);

  const trimmed = name.trim();
  const nameProblem =
    trimmed.length === 0 || (trimmed.length >= 2 && trimmed.length <= 40)
      ? null
      : "2~40자로 입력해 주세요.";
  const canSubmit = trimmed.length >= 2 && trimmed.length <= 40 && !submitting;

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const team = await createTeam({ name: trimmed, ...(role ? { role } : {}) });
      setCreated({ teamId: team.team_id, name: team.name });
      rememberTeam(team.team_id);
      // The lists on the page loaded before this team existed. A list that
      // cannot be read again leaves them as they were until the next load.
      listTeams()
        .then(announceTeams)
        .catch(() => undefined);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "팀을 만들지 못했습니다.");
      setSubmitting(false);
    }
  };

  const panel = (
    <div
      ref={box}
      role="dialog"
      aria-modal
      aria-label="새 팀 만들기"
      className="flex flex-col gap-3 rounded-[var(--radius)] border border-[var(--color-hairline)] bg-[var(--color-paper)]"
      style={{
        width: 380,
        maxWidth: "100%",
        maxHeight: "calc(100vh - 2 * var(--space-page))",
        overflowY: "auto",
        padding: "16px 18px",
        boxShadow: "var(--shadow-overlay)",
      }}
    >
      {created === null ? (
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (canSubmit) void submit();
          }}
        >
          <h2 className="text-[var(--color-ink-strong)]" style={{ ...LABEL, fontWeight: 600 }}>
            새 팀 만들기
          </h2>
          <label className="flex flex-col gap-1.5" style={LABEL}>
            팀 이름
            <input
              className={INPUT}
              style={INPUT_STYLE}
              value={name}
              autoFocus
              maxLength={60}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          {nameProblem !== null ? (
            <p role="alert" style={{ ...META, color: "var(--color-signal-critical)" }}>
              {nameProblem}
            </p>
          ) : null}
          <div className="flex flex-col gap-1.5" role="group" aria-label="내 직무">
            <span style={LABEL}>
              내 직무 <span style={{ fontWeight: 400, color: "var(--color-ink-muted)" }}>선택</span>
            </span>
            <div className="flex flex-wrap gap-1.5">
              {ROLES.map((option) => (
                <ChipToggle
                  key={option}
                  selected={role === option}
                  onClick={() => setRole(role === option ? null : option)}
                >
                  {option}
                </ChipToggle>
              ))}
            </div>
          </div>
          {error !== null ? (
            <p role="alert" style={{ ...META, color: "var(--color-signal-critical)" }}>
              {error}
            </p>
          ) : null}
          <div className="flex items-center justify-end gap-2">
            <Button tone="quiet" size="compact" type="button" onClick={onClose}>
              닫기
            </Button>
            <Button tone="primary" size="compact" type="submit" loading={submitting} disabled={!canSubmit}>
              팀 만들기
            </Button>
          </div>
        </form>
      ) : (
        <>
          <div>
            <h2 className="text-[var(--color-ink-strong)]" style={{ ...LABEL, fontWeight: 600 }}>
              팀원 초대
            </h2>
            <p className="mt-1" role="status" style={META}>
              {created.name} 팀을 만들었습니다. 지금은 나만 들어가 있습니다.
            </p>
          </div>
          <TeamInvite teamId={created.teamId} />
          <div className="flex items-center justify-between gap-3">
            <span style={META}>나중에 설정 › 구성원에서도 초대할 수 있습니다.</span>
            <Button tone="primary" size="compact" type="button" onClick={onClose}>
              완료
            </Button>
          </div>
        </>
      )}
    </div>
  );

  return createPortal(
    <div
      data-testid="new-team-backdrop"
      className="fixed inset-0 z-50 flex items-center justify-center"
      // The action item window's own dimming and margin.
      style={{ background: "rgba(22,25,31,.35)", padding: "var(--space-page)" }}
    >
      {panel}
    </div>,
    document.body,
  );
}
