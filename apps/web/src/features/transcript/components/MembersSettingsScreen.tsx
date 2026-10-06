"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/shared/api/client";
import { Button } from "@/shared/ui";

import {
  cancelInvitation,
  leaveTeam,
  listPendingInvitations,
  listTeamMembers,
  type PendingInvitation,
} from "../api";
import { rememberTeam } from "../selectedTeam";
import type { TeamMember } from "../types";
import { TeamInvite } from "./TeamInvite";
import { TeamScope } from "./TeamScope";

/**
 * 설정 › 구성원 (#552): who is on the workspace, and inviting somebody to it.
 *
 * S02 offers the invitation right after a workspace is made; this is the same
 * control for a workspace that already exists -- the one a team has connected
 * its tools to and holds its meetings in. Without it, inviting anybody meant
 * making a new, empty workspace.
 *
 * The list is the team's members by name, as the speaker picker already shows
 * them.
 *
 * **Pending invitations are listed, with a way to take one back** (the
 * follow-up left on #552). What a member sees is what the inviter typed and
 * the team already holds -- the address, when the link lapses, who invited --
 * and never the link. The section is absent when nothing is pending, and when
 * the list cannot be read: an invitation that looked cancellable and was not
 * would be worse than none shown.
 *
 * **Leaving is the person's own act, said before it is done.** One press
 * opens what leaving means -- the team's meetings can no longer be read; what
 * they said and the items they hold stay with the team, and their own words
 * are still theirs to delete afterwards (설정 › 개인정보 · 보관; the right does
 * not go with the membership) -- and a second press does it. The last member
 * is refused by the server and told why.
 */

const SECTION_TITLE = {
  fontSize: "var(--text-label)",
  fontWeight: "var(--text-label-weight)",
  color: "var(--color-ink-body)",
} as const;
const META = { fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" } as const;

export function MembersSettingsScreen() {
  return (
    <main style={{ padding: "var(--space-24) var(--space-page) var(--space-page)" }}>
      <div className="max-w-[760px]">
        {/* A team is the unit a project gets today: its own members, its own
            meetings. Someone already on a team had no way back to S02 short of
            typing its address. */}
        <p className="mb-4" style={META}>
          프로젝트마다 구성원을 따로 두려면 팀을 하나 더 만드세요.{" "}
          <Link href="/workspace/new" className="text-[var(--color-accent-default)]">
            새 팀 만들기
          </Link>
        </p>
        <TeamScope>{(teamId) => <Members key={teamId} teamId={teamId} />}</TeamScope>
      </div>
    </main>
  );
}

function Members({ teamId }: { teamId: string }) {
  const [members, setMembers] = useState<TeamMember[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [pending, setPending] = useState<PendingInvitation[] | null>(null);
  const [cancelling, setCancelling] = useState<number | null>(null);
  const [cancelFailed, setCancelFailed] = useState(false);
  const [asking, setAsking] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [leaveNote, setLeaveNote] = useState<string | null>(null);

  const readPending = useCallback(() => {
    listPendingInvitations(teamId)
      .then(setPending)
      // Not read is not shown: the section stays off.
      .catch(() => setPending(null));
  }, [teamId]);

  useEffect(() => {
    readPending();
  }, [readPending]);

  const cancel = async (invitationId: number) => {
    setCancelling(invitationId);
    setCancelFailed(false);
    try {
      setPending(await cancelInvitation(teamId, invitationId));
    } catch {
      setCancelFailed(true);
    } finally {
      setCancelling(null);
    }
  };

  const leave = async () => {
    setLeaving(true);
    setLeaveNote(null);
    try {
      const left = await leaveTeam(teamId);
      // Every screen holds the team list it read at start: go where the
      // person now belongs and read it again from there.
      const next = left[0]?.team_id;
      if (next) rememberTeam(next);
      window.location.assign(next ? "/" : "/workspace/new");
    } catch (error) {
      setLeaveNote(
        error instanceof ApiError && error.code === "last_team_member"
          ? "이 팀에 남은 구성원이 나뿐이라 나갈 수 없습니다. 다른 사람을 초대해 그 사람이 들어온 뒤에 나갈 수 있습니다."
          : "팀에서 나가지 못했습니다. 잠시 후 다시 시도해 주세요.",
      );
      setLeaving(false);
    }
  };

  useEffect(() => {
    let current = true;
    listTeamMembers(teamId)
      .then((list) => {
        if (current) setMembers(list);
      })
      .catch(() => {
        if (current) setFailed(true);
      });
    return () => {
      current = false;
    };
  }, [teamId]);

  return (
    <>
      <section aria-labelledby="members-title">
        <h2 id="members-title" style={SECTION_TITLE}>
          구성원
        </h2>
        {failed ? (
          <p className="mt-2" style={META}>
            구성원 목록을 불러오지 못했습니다.
          </p>
        ) : members === null ? (
          <p className="mt-2" style={META}>
            구성원을 불러오는 중입니다.
          </p>
        ) : (
          <ul className="mt-2 border-t border-[var(--color-hairline)]">
            {members.map((member) => (
              <li
                key={member.user_id}
                className="border-b border-[var(--color-hairline)] text-[var(--color-ink-strong)]"
                style={{ paddingBlock: "var(--space-8)", fontSize: "var(--text-rowBody)" }}
              >
                {member.name}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-8" aria-labelledby="invite-title">
        <h2 id="invite-title" className="mb-2" style={SECTION_TITLE}>
          팀원 초대
        </h2>
        <TeamInvite teamId={teamId} canConnectMail onInvited={readPending} />
      </section>

      {pending !== null && pending.length > 0 ? (
        <section className="mt-8" aria-labelledby="pending-title">
          <h2 id="pending-title" style={SECTION_TITLE}>
            수락을 기다리는 초대
          </h2>
          <ul className="mt-2 border-t border-[var(--color-hairline)]">
            {pending.map((invitation) => (
              <li
                key={invitation.id}
                className="flex flex-wrap items-center gap-3 border-b border-[var(--color-hairline)]"
                style={{ paddingBlock: "var(--space-8)" }}
              >
                <span
                  className="text-[var(--color-ink-strong)]"
                  style={{ fontSize: "var(--text-rowBody)" }}
                >
                  {invitation.email}
                </span>
                <span style={META}>
                  {invitation.invited_by_name ? `${invitation.invited_by_name} 님이 초대 · ` : ""}
                  {lapses(invitation.expires_at)}까지
                </span>
                <span className="ml-auto">
                  <Button
                    tone="quiet"
                    size="compact"
                    loading={cancelling === invitation.id}
                    onClick={() => cancel(invitation.id)}
                    aria-label={`${invitation.email} 초대 취소`}
                  >
                    초대 취소
                  </Button>
                </span>
              </li>
            ))}
          </ul>
          {cancelFailed ? (
            <p role="alert" className="mt-2" style={META}>
              초대를 취소하지 못했습니다. 잠시 후 다시 시도해 주세요.
            </p>
          ) : null}
        </section>
      ) : null}

      <section className="mt-8" aria-labelledby="leave-title">
        <h2 id="leave-title" className="mb-2" style={SECTION_TITLE}>
          팀 나가기
        </h2>
        {asking ? (
          <>
            <p style={META}>
              나가면 이 팀의 회의와 기록을 더 볼 수 없습니다. 이 팀 회의에서 내가 한 말과 내가
              담당한 항목은 팀의 기록으로 남습니다. 내가 한 말은 나간 뒤에도 설정의
              &lsquo;개인정보 · 보관&rsquo;에서 직접 삭제할 수 있습니다. 다시 들어오려면 팀원의 초대가
              필요합니다.
            </p>
            <div className="mt-2 flex gap-2">
              <Button tone="quiet" size="compact" loading={leaving} onClick={leave}>
                이 팀에서 나가기
              </Button>
              <Button tone="text" size="compact" onClick={() => setAsking(false)}>
                취소
              </Button>
            </div>
          </>
        ) : (
          <Button tone="text" size="compact" onClick={() => setAsking(true)}>
            이 팀에서 나가기
          </Button>
        )}
        {leaveNote ? (
          <p role="alert" className="mt-2" style={META}>
            {leaveNote}
          </p>
        ) : null}
      </section>
    </>
  );
}

/** The day a link lapses, as a date: the hour says little a week ahead. */
function lapses(expiresAt: string): string {
  const day = new Date(expiresAt);
  return Number.isNaN(day.getTime())
    ? expiresAt
    : day.toLocaleDateString("ko-KR", { month: "long", day: "numeric" });
}
