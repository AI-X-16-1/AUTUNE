"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/shared/api/client";
import { Button } from "@/shared/ui";

import {
  cancelInvitation,
  deleteTeam,
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
 * not go with the membership), though only everywhere at once and without
 * being able to open this team's meetings first -- and a second press does it.
 *
 * **The one person left on a team is offered "팀 삭제" in its place** (#1007;
 * the user and the four other owners, 2026-10-09). They cannot leave -- a team
 * with nobody on it could be neither read nor deleted -- so what the section
 * holds for them is the team's deletion. One press opens what it means, and
 * the order of that text is the decision's: what goes (everything Autune keeps
 * for the team, the lines of people who left it earlier among them, and what
 * Autune put on their own calendar), then what stays because it is outside
 * Autune -- the minutes, pages and issues in the team's Notion, Slack and
 * Jira, the gap notices and the reports in the team's Slack channel -- and
 * that after this nothing in Autune can take those back. Then the team's name
 * is typed. The server compares it; the button only waits for something to
 * have been typed, so there is one rule for what counts as the same name.
 *
 * Which of the two the section shows follows the member list. Until that list
 * has been read it shows leaving, and the server's own refusal moves it: a
 * last member who presses leave is shown the deletion, and a deletion refused
 * because somebody joined reads the list again.
 */

const SECTION_TITLE = {
  fontSize: "var(--text-label)",
  fontWeight: "var(--text-label-weight)",
  color: "var(--color-ink-body)",
} as const;
const META = { fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" } as const;
const INPUT =
  "w-full rounded-[var(--radius)] bg-[var(--color-surface-panel)] px-3 text-[var(--color-ink-strong)] focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)]";
const INPUT_STYLE = {
  height: "var(--control-h-default)",
  fontSize: "var(--text-rowBody)",
  border: "1px solid var(--color-hairline)",
} as const;

/** What the server said, as the person can act on it. */
const DELETE_REFUSED: Record<string, string> = {
  team_name_mismatch:
    "입력한 이름이 팀 이름과 다릅니다. 띄어쓰기와 대소문자까지 그대로 입력해 주세요.",
  team_meeting_in_progress:
    "전사 중이거나 실시간으로 진행 중인 회의가 있어 팀을 삭제할 수 없습니다. 끝난 뒤에 다시 시도해 주세요. 전사가 멈춰 있다면 그 회의에서 전사를 취소한 뒤 삭제할 수 있습니다.",
  team_has_other_members:
    "이 팀에 다른 구성원이 있어 삭제할 수 없습니다. 팀은 혼자 남은 사람만 삭제할 수 있습니다.",
};

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
  // The server said so, whatever the list on screen says.
  const [toldAlone, setToldAlone] = useState(false);
  const [typed, setTyped] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteNote, setDeleteNote] = useState<string | null>(null);

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
      if (error instanceof ApiError && error.code === "last_team_member") {
        // The section becomes the deletion; what it means is not yet shown.
        setToldAlone(true);
        setAsking(false);
        setLeaveNote(
          "이 팀에 남은 구성원이 나뿐이라 나갈 수 없습니다. 다른 사람을 초대해 그 사람이 들어온 뒤에 나가거나, 팀을 삭제할 수 있습니다.",
        );
      } else {
        setLeaveNote("팀에서 나가지 못했습니다. 잠시 후 다시 시도해 주세요.");
      }
      setLeaving(false);
    }
  };

  const readMembers = useCallback(
    (current: () => boolean = () => true) => {
      listTeamMembers(teamId)
        .then((list) => {
          if (current()) setMembers(list);
        })
        .catch(() => {
          if (current()) setFailed(true);
        });
    },
    [teamId],
  );

  const remove = async () => {
    setDeleting(true);
    setDeleteNote(null);
    setLeaveNote(null);
    try {
      const left = await deleteTeam(teamId, typed);
      // As after leaving: go where the person now belongs.
      const next = left[0]?.team_id;
      if (next) rememberTeam(next);
      window.location.assign(next ? "/" : "/workspace/new");
    } catch (error) {
      const code = error instanceof ApiError ? error.code : null;
      if (code === "team_has_other_members") {
        // Somebody joined: the list on screen is old, and so is this section.
        setToldAlone(false);
        setAsking(false);
        setTyped("");
        readMembers();
      }
      // Not "nothing was deleted": a request that did not come back may
      // have been carried out.
      setDeleteNote(
        (code && DELETE_REFUSED[code]) ?? "팀을 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.",
      );
      setDeleting(false);
    }
  };

  useEffect(() => {
    let current = true;
    readMembers(() => current);
    return () => {
      current = false;
    };
  }, [readMembers]);

  const alone = toldAlone || members?.length === 1;

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
                  {invitation.email ?? "주소 없는 링크"}
                </span>
                <span style={META}>
                  {invitation.invited_by_name ? `${invitation.invited_by_name} 님이 초대 · ` : ""}
                  {invitation.email === null
                    ? `${clock(invitation.expires_at)}까지, 한 번만`
                    : `${lapses(invitation.expires_at)}까지`}
                </span>
                <span className="ml-auto">
                  <Button
                    tone="quiet"
                    size="compact"
                    loading={cancelling === invitation.id}
                    onClick={() => cancel(invitation.id)}
                    aria-label={`${invitation.email ?? "주소 없는 링크"} 초대 취소`}
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
          {alone ? "팀 삭제" : "팀 나가기"}
        </h2>
        {alone ? (
          asking ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (typed.trim() && !deleting) void remove();
              }}
            >
              <p style={META}>
                이 팀에 남은 구성원이 나뿐이라 나갈 수는 없고, 팀을 삭제할 수 있습니다. 삭제하면
                이 팀과 팀의 모든 회의, 전사, 할 일, 결정 사항, 갭 리포트, 리포트, 자료
                목록을 비롯해 Autune이 이 팀에 대해 보관하는 것이 모두 삭제됩니다. 먼저 팀에서
                나간 사람들이 이 팀 회의에서 한 말도 함께 삭제되며, 그 사람들에게 알림은 가지
                않습니다. 팀에 연결한 Slack, Notion, Jira 연동과 아직 수락되지 않은 초대도
                삭제되고, Autune이 내 Google Calendar에 넣은 일정은 삭제를 요청합니다. 삭제한
                뒤에는 되돌릴 수 없습니다.
              </p>
              <p className="mt-2" style={META}>
                팀의 도구에 이미 보낸 것은 삭제되지 않고 그 도구에 남습니다. Notion, Slack,
                Jira에 보낸 프로젝트별 회의록과 할 일·결정 사항의 Notion 페이지와 Jira
                이슈, 팀 Slack 채널에 올라간 갭 질문과 다음 회의 안내, 팀 Slack 채널에 올라간
                회의 리포트와 주간 팀 리포트가 그렇습니다. 각자 Slack 개인 메시지로 받은 알림도
                남고, 자료로 등록한 Google Drive 파일도 Drive에 그대로 있습니다. 팀을 삭제하면 Autune에서는 이것들을 더 지울 수 없으니,
                지워야 하는 것이 있으면 그 도구에서 직접 지워 주세요.
              </p>
              <label className="mt-3 block" style={META}>
                삭제하려면 이 팀의 이름을 입력해 주세요.
                <input
                  type="text"
                  value={typed}
                  onChange={(event) => setTyped(event.target.value)}
                  maxLength={200}
                  autoComplete="off"
                  className={`${INPUT} mt-1 block max-w-[360px]`}
                  style={INPUT_STYLE}
                />
              </label>
              <div className="mt-2 flex gap-2">
                <Button
                  tone="primary"
                  size="compact"
                  type="submit"
                  loading={deleting}
                  disabled={!typed.trim()}
                >
                  이 팀 삭제
                </Button>
                <Button
                  tone="quiet"
                  size="compact"
                  type="button"
                  onClick={() => {
                    setAsking(false);
                    setTyped("");
                    setDeleteNote(null);
                  }}
                >
                  취소
                </Button>
              </div>
            </form>
          ) : (
            <Button tone="destructiveText" size="compact" onClick={() => setAsking(true)}>
              이 팀 삭제
            </Button>
          )
        ) : asking ? (
          <>
            <p style={META}>
              나가면 이 팀의 회의와 기록을 더 볼 수 없습니다. 이 팀 회의에서 내가 한 말과 내가
              담당한 항목은 팀의 기록으로 남습니다. 내가 한 말은 나간 뒤에도 설정의
              &lsquo;개인정보 · 보관&rsquo;에서 직접 삭제할 수 있습니다. 다만 그 삭제는 이 팀만
              골라서 할 수 없고 모든 팀에서 내가 한 말이 한꺼번에 삭제되며, 나간 뒤에는 이 팀의
              회의를 열어 확인할 수 없습니다. 내가 이 팀에 보낸 초대 중 아직 수락되지 않은 것은
              함께 취소됩니다. 다시 들어오려면 팀원의 초대가 필요합니다.
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
        {(deleteNote ?? leaveNote) ? (
          <p role="alert" className="mt-2" style={META}>
            {deleteNote ?? leaveNote}
          </p>
        ) : null}
      </section>
    </>
  );
}

/** `14:05`: when a link that lasts an hour stops working -- a date would say "today". */
function clock(expiresAt: string): string {
  const at = new Date(expiresAt);
  if (Number.isNaN(at.getTime())) return expiresAt;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

/** The day a link lapses, as a date: the hour says little a week ahead. */
function lapses(expiresAt: string): string {
  const day = new Date(expiresAt);
  return Number.isNaN(day.getTime())
    ? expiresAt
    : day.toLocaleDateString("ko-KR", { month: "long", day: "numeric" });
}
