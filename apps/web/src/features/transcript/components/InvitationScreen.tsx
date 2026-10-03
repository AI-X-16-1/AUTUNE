"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { getSession, type SessionUser } from "@/shared/api/auth";
import { setSignedIn } from "@/shared/api/client";
import { Button } from "@/shared/ui";

import { acceptInvitation } from "../api";
import { forgetToken, takeToken } from "../invitationLink";

/**
 * Where an invitation link lands (#552): the invited person, signed in under
 * the invited address, accepts -- and only then joins the team.
 *
 * **Accepting is a button, not the page load.** Joining a team changes who can
 * read what in both directions, so it is the person's own act; opening a link
 * is not one.
 *
 * **Signed out, it offers sign-in and comes back here.** The route passes the
 * sign-in card in (`signIn`): it belongs to the app, and a feature does not
 * import the app. The token waits in `sessionStorage` meanwhile
 * (`invitationLink`).
 *
 * **A refusal says nothing about the invitation.** The server gives one answer
 * for an unknown link, a used one, an expired one and one sent to another
 * address, and this shows one sentence for it. What it does show is which
 * account is signed in, since the usual cause is the wrong one.
 *
 * The team's name is not shown before accepting: there is no route that reads
 * an invitation, on purpose, so the page learns it only from the acceptance.
 */

type Session = { status: "checking" } | { status: "out" } | { status: "in"; user: SessionUser };
type Phase =
  | { kind: "idle" }
  | { kind: "accepting" }
  | { kind: "joined"; team: string }
  | { kind: "refused" };

const BODY = { fontSize: "var(--text-body)", lineHeight: "var(--text-body-leading)" } as const;
const META = { fontSize: "var(--text-meta)" } as const;

export function InvitationScreen({ signIn }: { signIn: ReactNode }) {
  const router = useRouter();
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const [session, setSession] = useState<Session>({ status: "checking" });
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  useEffect(() => {
    let current = true;
    // In an effect, not during render: the address bar and the storage exist
    // only in the browser.
    const found = takeToken();
    void getSession().then((user) => {
      if (!current) return;
      // A session found here switches the developer token off, as SessionGate does.
      setSignedIn(user !== null);
      setToken(found);
      setSession(user ? { status: "in", user } : { status: "out" });
    });
    return () => {
      current = false;
    };
  }, []);

  const accept = async () => {
    if (!token) return;
    setPhase({ kind: "accepting" });
    try {
      const team = await acceptInvitation(token);
      forgetToken();
      setPhase({ kind: "joined", team: team.name });
    } catch {
      forgetToken();
      setPhase({ kind: "refused" });
    }
  };

  const leave = () => {
    forgetToken();
    router.replace("/");
  };

  if (token === undefined || session.status === "checking") return null;

  return (
    <main
      className="grid min-h-screen place-items-center bg-[var(--color-surface-paper)]"
      style={{ padding: "var(--space-24)" }}
    >
      <div
        className="flex w-full max-w-[560px] flex-col rounded-[var(--radius)] bg-[var(--color-surface-panel)]"
        style={{ padding: "var(--space-32)", gap: 16, boxShadow: "var(--shadow-overlay)" }}
      >
        <h1
          className="text-[var(--color-ink-strong)]"
          style={{ fontSize: "var(--text-title)", fontWeight: "var(--text-title-weight)" }}
        >
          워크스페이스 초대
        </h1>

        {token === null ? (
          <p className="text-[var(--color-ink-body)]" style={BODY}>
            초대 링크가 올바르지 않습니다. 받은 링크를 그대로 열어 주세요.
          </p>
        ) : session.status === "out" ? (
          <>
            <p className="text-[var(--color-ink-body)]" style={BODY}>
              초대를 수락하려면 초대받은 이메일 주소로 로그인해 주세요. 로그인하면 이 화면으로
              돌아옵니다.
            </p>
            {signIn}
          </>
        ) : phase.kind === "joined" ? (
          <>
            <p role="status" className="text-[var(--color-ink-body)]" style={BODY}>
              {phase.team} 워크스페이스에 참여했습니다.
            </p>
            <div>
              <Button tone="primary" onClick={() => router.replace("/")}>
                시작하기
              </Button>
            </div>
          </>
        ) : phase.kind === "refused" ? (
          <>
            <p role="alert" className="text-[var(--color-ink-body)]" style={BODY}>
              이 초대 링크는 쓸 수 없습니다. 만료되었거나 이미 사용되었거나, 다른 주소로 보낸
              초대일 수 있습니다. 초대한 사람에게 새 링크를 요청해 주세요.
            </p>
            <p className="text-[var(--color-ink-muted)]" style={META}>
              지금 로그인한 계정: {session.user.email}
            </p>
            <div>
              <Button tone="secondary" onClick={() => router.replace("/")}>
                홈으로
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="text-[var(--color-ink-body)]" style={BODY}>
              수락하면 초대한 워크스페이스의 팀원이 되어 그 팀의 회의와 분석을 볼 수 있습니다. 그
              워크스페이스에서 내가 여는 회의도 팀원이 볼 수 있습니다.
            </p>
            <p className="text-[var(--color-ink-muted)]" style={META}>
              지금 로그인한 계정: {session.user.email}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                tone="primary"
                onClick={() => void accept()}
                loading={phase.kind === "accepting"}
                disabled={phase.kind === "accepting"}
              >
                초대 수락
              </Button>
              <Button tone="text" onClick={leave} disabled={phase.kind === "accepting"}>
                수락하지 않고 홈으로
              </Button>
            </div>
          </>
        )}
      </div>
    </main>
  );
}
