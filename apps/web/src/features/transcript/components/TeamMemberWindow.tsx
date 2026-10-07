"use client";

import { useEffect, useRef, type RefObject } from "react";
import { createPortal } from "react-dom";

import { Button } from "@/shared/ui";

import { TeamInvite } from "./TeamInvite";

/**
 * Inviting members to the team on the home screen, in a window in the middle
 * of the screen (the user, 2026-10-07: "팀 누르고 내부에 팀들이 왼쪽에 있고
 * 오른쪽 빈 공간에 팀원 추가 버튼 만들고 누르면 팀원 추가할 수 있게 가운데에
 * 화면 띄워죠").
 *
 * **It is 설정 › 구성원's "팀원 초대" in a window, not a second way of adding
 * anybody.** The same control (`TeamInvite`), the same calls, the same rules:
 * an invitation adds nobody until the invited person accepts it (#552), and a
 * link made for no address lets in whoever signs in with it, once, within the
 * hour (#919). "추가" is the user's word for the button; what the window does
 * is invite, and its control says so.
 *
 * **For one team, named in the heading**: the one the home screen is showing.
 * The window does not choose a team and cannot be opened without one.
 *
 * Connecting Gmail is not offered here, as in `NewTeamWindow`: it leaves the
 * page for Google and comes back, and a window would not survive that. It is
 * offered under 설정 › 구성원, where the pending invitations are listed too.
 *
 * Drawn on `document.body` over a dimmed page, the form the sidebar's "+"
 * window and an action item's window have. It closes on "닫기", on Escape and
 * on a press on the dimmed page; the invitations made so far stand.
 */

const META = { fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" } as const;

export function TeamMemberWindow({
  teamId,
  teamName,
  opener,
  onClose,
}: {
  teamId: string;
  teamName: string;
  /** The control that opened this: a press on it is not a press outside. */
  opener: RefObject<HTMLElement | null>;
  onClose: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);

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

  return createPortal(
    <div
      data-testid="team-member-backdrop"
      className="fixed inset-0 z-50 flex items-center justify-center"
      // The action item window's own dimming and margin.
      style={{ background: "rgba(22,25,31,.35)", padding: "var(--space-page)" }}
    >
      <div
        ref={box}
        role="dialog"
        aria-modal
        aria-label="팀원 추가"
        className="flex flex-col gap-3 rounded-[var(--radius)] border border-[var(--color-hairline)] bg-[var(--color-paper)]"
        style={{
          width: 440,
          maxWidth: "100%",
          maxHeight: "calc(100vh - 2 * var(--space-page))",
          overflowY: "auto",
          padding: "16px 18px",
          boxShadow: "var(--shadow-overlay)",
        }}
      >
        <div>
          <h2
            className="text-[var(--color-ink-strong)]"
            style={{ fontSize: "var(--text-label)", fontWeight: 600 }}
          >
            팀원 추가
          </h2>
          <p className="mt-1" style={META}>
            {teamName} 팀에 초대합니다.
          </p>
        </div>
        <TeamInvite teamId={teamId} />
        <div className="flex items-center justify-between gap-3">
          <span style={META}>보낸 초대와 Gmail 연결은 설정 › 구성원에 있습니다.</span>
          <Button tone="primary" size="compact" type="button" onClick={onClose}>
            닫기
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
