"use client";

import { useCallback, useEffect, useState } from "react";

import {
  disconnectNotion,
  getNotionConnection,
  notionConnectUrl,
  type NotionConnection,
} from "@/shared/api/auth";
import { Button } from "@/shared/ui";

import { getNotionSetup, setUpNotion, type NotionSetupState } from "../api";

/**
 * One button to connect the team's Notion workspace (#428). On Notion's screen
 * the person picks the pages Autune may see; back here, if they picked exactly
 * one, the databases are made under it and filled with everything already
 * confirmed without another click. With several, they choose one.
 *
 * A teamspace page is the safe parent: a private page goes with its owner, and
 * the databases with it (external-approvals.md).
 */
export function NotionConnect({ meetingId }: { meetingId: string }) {
  const [connection, setConnection] = useState<NotionConnection | null>(null);
  const [setup, setSetup] = useState<NotionSetupState | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const setUp = useCallback(
    async (pageId: string) => {
      setBusy(true);
      setNote("Notion에 DB를 만들고 확정된 내용을 넣는 중입니다…");
      try {
        const result = await setUpNotion(meetingId, pageId);
        const items = result.action_items.sent + result.action_items.replaced;
        const decisions = result.decisions.sent + result.decisions.replaced;
        setNote(`DB를 준비하고 액션 ${items}건, 결정 ${decisions}건을 넣었습니다.`);
        setSetup(await getNotionSetup(meetingId));
      } catch {
        setNote("DB를 만들지 못했습니다. 페이지를 Autune에 공유했는지 확인해 주세요.");
      } finally {
        setBusy(false);
      }
    },
    [meetingId],
  );

  useEffect(() => {
    let alive = true;
    void (async () => {
      const status = await getNotionConnection(meetingId);
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
      const state = await getNotionSetup(meetingId).catch(() => null);
      if (!alive) return;
      setSetup(state);
      // Straight from Notion with one page shared: finish without another click.
      const only = state?.pages?.length === 1 ? state.pages[0] : undefined;
      if (result === "connected" && state && !state.target && only) {
        await setUp(only.id);
      }
    })();
    return () => {
      alive = false;
    };
  }, [meetingId, setUp]);

  if (connection === null) return null;
  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  const status = note ? (
    <span role="status" className="text-[var(--color-ink-muted)]" style={meta}>
      {note}
    </span>
  ) : null;

  const connect = () => {
    const here = window.location.pathname + window.location.search;
    window.location.assign(notionConnectUrl(meetingId, here));
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
            액션 DB
          </a>
          <a className="text-[var(--color-accent-default)]" style={meta} href={setup.target.decision_db_url} target="_blank" rel="noopener noreferrer">
            결정 DB
          </a>
        </>
      ) : null}
      {setup && !setup.target && setup.pages && setup.pages.length === 0 ? (
        <span className="text-[var(--color-signal-critical)]" style={meta}>
          Autune에 공유된 페이지가 없습니다. 다시 연결하면서 팀스페이스 페이지를 하나 골라 주세요.
        </span>
      ) : null}
      {setup && !setup.target && setup.pages && setup.pages.length > 1 ? (
        <label className="flex items-center gap-2 text-[var(--color-ink-muted)]" style={meta}>
          DB를 만들 페이지
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
          void disconnectNotion(meetingId)
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
