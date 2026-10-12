import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NotionConnect } from "./NotionConnect";
import type { NotionSetupState } from "../api";

// A team that already has its databases can ask for the setup once more. The
// server keeps the databases and asks Notion about each again, which is the
// only thing that does after a refusal was recorded (review of #1195): until
// now the screen ran the setup only while it showed no databases.

const getNotionSetup = vi.fn<() => Promise<NotionSetupState>>();
const setUpNotion = vi.fn<(scope: unknown, pageId?: string) => Promise<unknown>>();
vi.mock("../api", () => ({
  getNotionSetup: () => getNotionSetup(),
  setUpNotion: (scope: unknown, pageId?: string) => setUpNotion(scope, pageId),
}));
vi.mock("@/shared/api/auth", () => ({
  getNotionConnection: () => Promise.resolve({ connected: true, workspace_name: "팀 워크스페이스" }),
  disconnectNotion: vi.fn(),
  notionConnectUrl: () => "/connect",
}));

afterEach(() => {
  cleanup();
  getNotionSetup.mockReset();
  setUpNotion.mockReset();
});

const SET_UP: NotionSetupState = {
  connected: true,
  pages: [{ id: "page_1", title: "팀 페이지" }],
  target: {
    parent_page_id: "page_1",
    action_db_url: "https://notion.test/actions",
    decision_db_url: "https://notion.test/decisions",
    minutes_db_url: "https://notion.test/minutes",
  },
};

describe("a team whose Notion databases are already set up", () => {
  it("can have them checked again, under the page they are in", async () => {
    getNotionSetup.mockResolvedValue(SET_UP);
    setUpNotion.mockResolvedValue({ databases: "reused" });
    render(<NotionConnect teamId="team_1" />);

    const again = await screen.findByRole("button", { name: "DB 다시 확인" });
    expect(screen.getByText("Notion에서 권한을 다시 공유했다면")).toBeTruthy();
    expect(setUpNotion).not.toHaveBeenCalled();

    fireEvent.click(again);

    await waitFor(() => expect(setUpNotion).toHaveBeenCalledTimes(1));
    expect(setUpNotion).toHaveBeenCalledWith({ teamId: "team_1" }, "page_1");
    // It says what was done -- checked, not made -- and that the rows go in again.
    const note = await screen.findByText(/^DB를 다시 확인했습니다\./);
    expect(note.textContent).toContain("다시 넣고 있습니다");
    expect(screen.queryByText(/DB를 준비했습니다/)).toBeNull();
    // The databases are read again after it: once on arrival, once now.
    expect(getNotionSetup).toHaveBeenCalledTimes(2);
  });

  it("says the check failed, not that nothing could be made", async () => {
    getNotionSetup.mockResolvedValue(SET_UP);
    setUpNotion.mockRejectedValue(new Error("refused"));
    render(<NotionConnect teamId="team_1" />);

    fireEvent.click(await screen.findByRole("button", { name: "DB 다시 확인" }));

    expect(await screen.findByText(/^DB를 확인하지 못했습니다\./)).toBeTruthy();
    expect(screen.queryByText(/DB를 만들지 못했습니다/)).toBeNull();
    // Still there to press again.
    expect(screen.getByRole("button", { name: "DB 다시 확인" })).toBeTruthy();
  });
});

describe("a team with no databases yet", () => {
  it("is offered a page to choose and nothing to check again", async () => {
    getNotionSetup.mockResolvedValue({
      connected: true,
      pages: [
        { id: "page_1", title: "팀 페이지" },
        { id: "page_2", title: "다른 페이지" },
      ],
      target: null,
    });
    render(<NotionConnect teamId="team_1" />);

    expect(await screen.findByText("Autune 페이지를 만들 위치")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "DB 다시 확인" })).toBeNull();
    expect(screen.queryByText("Notion에서 권한을 다시 공유했다면")).toBeNull();
  });
});
