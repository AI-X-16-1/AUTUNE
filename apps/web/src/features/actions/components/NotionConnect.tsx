"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  disconnectNotion,
  getNotionConnection,
  notionConnectUrl,
  type NotionConnection,
  type IntegrationScope,
} from "@/shared/api/auth";
import { Button } from "@/shared/ui";

import { getNotionSetup, setUpNotion, type NotionSetupState } from "../api";

/**
 * One button to connect the team's Notion workspace (#428). On Notion's screen
 * the person picks the pages Autune may see; back here, if they picked exactly
 * one, the databases are made under it without another click, and everything
 * already confirmed goes in from the worker (#481). With several, they choose one.
 *
 * A teamspace page is the safe parent: a private page goes with its owner, and
 * the databases with it (external-approvals.md).
 *
 * Takes the meeting the 할 일 tab shows, or the team itself on S28 settings
 * (#496); the server checks membership either way.
 */
export function NotionConnect({
  meetingId,
  teamId,
}: {
  meetingId?: string;
  teamId?: string;
}) {
  const scope = useMemo<IntegrationScope>(
    () => (meetingId !== undefined ? { meetingId } : { teamId: teamId ?? "" }),
    [meetingId, teamId],
  );
  const [connection, setConnection] = useState<NotionConnection | null>(null);
  const [setup, setSetup] = useState<NotionSetupState | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // `again`: the team already has its databases and asks for the setup once
  // more. The server keeps them and asks Notion about each afresh -- what a
  // team needs after it gave Autune a database back, since a refusal is
  // remembered and no sync asks twice (`notion_setup.ensure_content`).
  const setUp = useCallback(
    async (pageId?: string, again = false) => {
      setBusy(true);
      setNote(again ? "Notion의 DB를 다시 확인하는 중입니다…" : "Notion에 Autune 페이지와 DB를 만드는 중입니다…");
      try {
        await setUpNotion(scope, pageId);
        setNote(
          again
            ? "DB를 다시 확인했습니다. 확정된 할 일과 결정을 다시 넣고 있습니다 — 많으면 몇 분 걸립니다."
            : "Autune 페이지에 DB를 준비했습니다. 확정된 할 일과 결정을 넣고 있습니다 — 많으면 몇 분 걸립니다.",
        );
        setSetup(await getNotionSetup(scope));
      } catch {
        setNote(
          again
            ? "DB를 확인하지 못했습니다. 페이지를 Autune에 공유했는지 확인해 주세요."
            : "DB를 만들지 못했습니다. 페이지를 Autune에 공유했는지 확인해 주세요.",
        );
      } finally {
        setBusy(false);
      }
    },
    [scope],
  );

  useEffect(() => {
    let alive = true;
    void (async () => {
      const status = await getNotionConnection(scope);
      if (!alive) return;
      setConnection(status);
      const url = new URL(window.location.href);
      const result = url.searchParams.get("notion");
      if (result !== null) {
        url.searchParams.delete("notion");
        window.history.replaceState(null, "", url.toString());
      }
      if (result === "failed") setNote("Notion을 연결하지 못했습니다. 다시 시도해 주세요.");
      if (!status?.connected) return;
      const state = await getNotionSetup(scope).catch(() => null);
      if (!alive) return;
      setSetup(state);
      // Straight from Notion with one page shared, or none: finish without
      // another click. None makes the "Autune" page among the person's
      // private pages (decided with the user, 2026-10-01).
      const pages = state?.pages ?? [];
      // Not when Notion refused the token: the reconnect prompt is the answer,
      // and a setup would only fail beside it (#622 review).
      if (
        result === "connected" &&
        state &&
        !state.target &&
        !state.needs_reconnect &&
        pages.length <= 1
      ) {
        await setUp(pages[0]?.id);
      }
    })();
    return () => {
      alive = false;
    };
  }, [scope, setUp]);

  if (connection === null) return null;
  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  const status = note ? (
    <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>
      {note}
    </span>
  ) : null;

  const connect = () => {
    const here = window.location.pathname + window.location.search;
    window.location.assign(notionConnectUrl(scope, here));
  };

  if (!connection.connected) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <Button tone="text" size="compact" onClick={connect}>
          팀 Notion 연결
        </Button>
        {status}
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-[var(--color-ink-muted)]" style={meta}>
        Notion 연결됨 · {connection.workspace_name ?? "워크스페이스"}
      </span>
      {setup?.target ? (
        <>
          <a className="text-[var(--color-accent-default)]" style={meta} href={setup.target.action_db_url} target="_blank" rel="noopener noreferrer">
            할 일 DB
          </a>
          <a className="text-[var(--color-accent-default)]" style={meta} href={setup.target.decision_db_url} target="_blank" rel="noopener noreferrer">
            결정 DB
          </a>
          {/* The screen ran the setup only while it showed no databases, so a
              team that shared a database with Autune again had nothing to
              press (review of #1195). */}
          <span className="text-[var(--color-ink-muted)]" style={meta}>
            Notion에서 권한을 다시 공유했다면
          </span>
          <Button
            tone="text"
            size="compact"
            disabled={busy}
            onClick={() => void setUp(setup.target?.parent_page_id, true)}
          >
            DB 다시 확인
          </Button>
        </>
      ) : null}
      {setup && !setup.target && setup.pages && setup.pages.length === 0 ? (
        <>
          <span className="text-[var(--color-ink-muted)]" style={meta}>
            Autune에 공유된 페이지가 없습니다.
          </span>
          <Button tone="text" size="compact" disabled={busy} onClick={() => void setUp()}>
            내 개인 페이지에 만들기
          </Button>
        </>
      ) : null}
      {setup?.needs_reconnect ? (
        <>
          <span className="text-[var(--color-signal-critical)]" style={meta}>
            Notion이 연결을 거부했습니다. 다시 연결해 주세요.
          </span>
          <Button tone="text" size="compact" onClick={connect}>
            다시 연결
          </Button>
        </>
      ) : null}
      {/* Also after a reconnect elsewhere, or once the parent page is no longer
          shared: the stored databases are not used, and a page is chosen again. */}
      {setup && !setup.target && setup.pages && setup.pages.length >= 1 ? (
        <label className="flex items-center gap-2 text-[var(--color-ink-muted)]" style={meta}>
          Autune 페이지를 만들 위치
          <select disabled={busy} defaultValue="" onChange={(event) => void setUp(event.target.value)}>
            <option value="" disabled>
              선택
            </option>
            {setup.pages.map((page) => (
              <option key={page.id} value={page.id}>
                {page.title}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <Button
        tone="quiet"
        size="compact"
        loading={busy}
        onClick={() =>
          void disconnectNotion(scope)
            .then(() => {
              setConnection({ connected: false });
              setSetup(null);
              setNote("Notion 연결을 해제했습니다. Notion 설정 > 연결에서 Autune도 제거해 주세요.");
            })
            .catch(() => setNote("연결을 해제하지 못했습니다."))
        }
      >
        연결 해제
      </Button>
      {status}
    </div>
  );
}
