import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SyncLog } from "../types";
import { SyncLogDrawer } from "./SyncLogDrawer";

// S28's "동기화 기록": the team's standing failures and its latest copies, as
// the server sent them. It retries nothing and draws only what it was sent.

const getSyncLog = vi.fn<(teamId: string) => Promise<SyncLog>>();
vi.mock("../api", () => ({ getSyncLog: (teamId: string) => getSyncLog(teamId) }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const ROW = {
  action_item_id: "act_1",
  meeting_id: "mtg/1",
  meeting_title: "주간 회의",
  description: "스펙 초안 공유",
};
const failure = (over: Partial<SyncLog["failures"][number]> = {}): SyncLog["failures"][number] => ({
  ...ROW,
  system: "jira",
  kind: "rejected",
  failed_at: "2026-10-08T09:00:00Z",
  ...over,
});
const copy = (over: Partial<SyncLog["copies"][number]> = {}): SyncLog["copies"][number] => ({
  ...ROW,
  system: "notion",
  url: "https://www.notion.so/page-1",
  copied_at: "2026-10-08T10:00:00Z",
  ...over,
});

const open = (log: SyncLog, onClose = vi.fn()) => {
  getSyncLog.mockResolvedValue(log);
  render(<SyncLogDrawer teamId="team_a" onClose={onClose} />);
  return onClose;
};
const failures = async () => within(await screen.findByRole("region", { name: "보내지 못한 것" }));
const copies = async () => within(await screen.findByRole("region", { name: "최근에 보낸 것" }));

afterEach(() => {
  cleanup();
  getSyncLog.mockReset();
});

describe("SyncLogDrawer", () => {
  it("asks for the team it was opened for", async () => {
    open({ failures: [], copies: [] });

    await failures();
    expect(getSyncLog).toHaveBeenCalledTimes(1);
    expect(getSyncLog).toHaveBeenCalledWith("team_a");
  });

  it("says a failure by its tool and its kind, and leads to the meeting's 액션 tab", async () => {
    open({ failures: [failure()], copies: [] });

    const list = await failures();
    expect(list.getByText("Jira · 요청이 거절되었습니다.")).toBeTruthy();
    const link = list.getByRole("link", { name: "스펙 초안 공유" });
    expect(link.getAttribute("href")).toBe("/meetings/mtg%2F1/actions");
    expect(list.getByText(/주간 회의/)).toBeTruthy();
    // Nothing is retried from this window.
    expect(screen.queryByRole("button", { name: "다시 시도" })).toBeNull();
  });

  it("asks the person to look in the tool first when there was no answer", async () => {
    open({ failures: [failure({ system: "notion", kind: "unreachable" })], copies: [] });

    expect((await failures()).getByText(/이미 만들어졌을 수 있으니/).textContent).toContain(
      "Notion에서 먼저 확인",
    );
  });

  it("says so when nothing stands, and when nothing was sent", async () => {
    open({ failures: [], copies: [] });

    expect((await failures()).getByText("지금 보내지 못한 채 남아 있는 것이 없습니다.")).toBeTruthy();
    expect((await copies()).getByText("아직 보낸 것이 없습니다.")).toBeTruthy();
  });

  it("opens a copy's page in a new tab", async () => {
    open({ failures: [], copies: [copy()] });

    const link = (await copies()).getByRole("link", { name: "Notion에서 열기" });
    expect(link.getAttribute("href")).toBe("https://www.notion.so/page-1");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noreferrer");
  });

  it.each(["javascript:alert(1)", "http://www.notion.so/page-1", "//evil.test/x"])(
    "names the tool and makes no link of an address that is not https (%s)",
    async (url) => {
      open({ failures: [], copies: [copy({ url })] });

      const list = await copies();
      expect(list.getByText(/Notion · 주간 회의/)).toBeTruthy();
      expect(list.queryByRole("link", { name: /열기/ })).toBeNull();
      expect(document.querySelector(`a[href="${url}"]`)).toBeNull();
    },
  );

  it("calls a calendar event the reader's own, with no link", async () => {
    open({ failures: [], copies: [copy({ system: "calendar", url: null })] });

    const list = await copies();
    expect(list.getByText(/내 캘린더 · 주간 회의/)).toBeTruthy();
    expect(list.queryByRole("link", { name: /열기/ })).toBeNull();
  });

  it("draws a masked value as a token, not as text to read", async () => {
    open({ failures: [failure({ description: "010-****-**** 로 연락" })], copies: [] });

    const link = (await failures()).getByRole("link");
    expect(link.textContent).toContain("로 연락");
    expect(link.querySelector("span, mark, code")).not.toBeNull();
  });

  it("says it could not read the log, and shows no empty lists", async () => {
    getSyncLog.mockRejectedValue(new Error("down"));
    render(<SyncLogDrawer teamId="team_a" onClose={vi.fn()} />);

    expect((await screen.findByRole("alert")).textContent).toContain(
      "동기화 기록을 불러오지 못했습니다.",
    );
    expect(screen.queryByText("아직 보낸 것이 없습니다.")).toBeNull();
    expect(screen.queryByText("지금 보내지 못한 채 남아 있는 것이 없습니다.")).toBeNull();
  });

  it("closes on its button, on Escape and on the backdrop, and not on a click inside", async () => {
    const onClose = open({ failures: [failure()], copies: [] });
    await failures();

    fireEvent.click(screen.getByText("보내지 못한 것"));
    expect(onClose).toHaveBeenCalledTimes(0);

    fireEvent.click(screen.getByRole("button", { name: "닫기" }));
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByRole("dialog", { name: "동기화 기록" }));
    expect(onClose).toHaveBeenCalledTimes(3);
  });
});
