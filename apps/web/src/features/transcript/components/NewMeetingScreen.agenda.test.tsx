import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NewMeetingScreen } from "./NewMeetingScreen";

// Where the form puts the agenda row (#1147): what the page lists is asked
// for the team chosen on the form, and none of it goes into the meeting or
// onto the form -- the draft is drawn in the pre-meeting brief.

const push = vi.fn<(href: string) => void>();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

type Created = { title: string; team_id: string; started_at?: string };
const create = vi.fn<(payload: Created) => Promise<{ meeting_id: string }>>();
vi.mock("../api", () => ({
  attestConsent: vi.fn(),
  createMeeting: (payload: Created) => create(payload),
  listTeams: () =>
    Promise.resolve([
      { team_id: "team_1", name: "검색팀", pinned: false },
      { team_id: "team_2", name: "결제팀", pinned: false },
    ]),
  uploadRecording: vi.fn(),
}));

afterEach(() => {
  cleanup();
  push.mockReset();
  create.mockReset();
});

const lines = vi.fn((teamId: string) =>
  Promise.resolve(teamId === "team_1" ? [{ title: "검색 응답 시간 개선" }] : []),
);
const SOURCES = [{ label: "열린 Jira 이슈", lines }];
// The live row is a status line (#1147): read by its name and the word it says, not by a role.
const live = () => screen.getByText(/어젠다 초안 자동 생성/).closest("div")!;
const on = () => within(live()).getByText(/^(켜짐|꺼짐)$/).textContent === "켜짐";

describe("NewMeetingScreen, the agenda row", () => {
  it("is S06's disabled Phase 2 row when the page lists nothing to draft from", async () => {
    render(<NewMeetingScreen />);
    await screen.findByRole("option", { name: "검색팀" });

    const row = screen.getByRole("checkbox", {
      name: /자료 연결 후 어젠다 자동 생성/,
    }) as HTMLInputElement;
    expect(row.disabled).toBe(true);
    expect(row.closest("label")!.textContent).toContain("Phase 2");
  });

  it("asks the page's sources for the team chosen on the form, and again when it changes", async () => {
    lines.mockClear();
    render(<NewMeetingScreen agendaSources={SOURCES} />);
    await screen.findByRole("option", { name: "검색팀" });

    await waitFor(() => expect(on()).toBe(true));
    expect(lines).toHaveBeenLastCalledWith("team_1");

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "team_2" } });

    await waitFor(() => expect(lines).toHaveBeenLastCalledWith("team_2"));
    await waitFor(() => expect(on()).toBe(false));
  });

  it("replaces S06's agenda row and leaves the end-of-meeting alert's row beside it", async () => {
    render(<NewMeetingScreen agendaSources={SOURCES} />);
    await screen.findByRole("option", { name: "검색팀" });

    expect(screen.queryByRole("checkbox", { name: /자료 연결 후/ })).toBeNull();
    // Module C's alert (#1152) is the other option row; whether it can be
    // ticked is its own test's business.
    expect(screen.getAllByRole("checkbox", { name: /종료 5분 전 미해결 갭 알림/ })).toHaveLength(1);
    expect(screen.getAllByText(/어젠다 초안 자동 생성/)).toHaveLength(1);
    // And it is not one of the form's checkboxes.
    expect(screen.queryByRole("checkbox", { name: /어젠다 초안/ })).toBeNull();
  });

  it("draws none of the draft on the form and puts nothing of it into the meeting it opens", async () => {
    create.mockResolvedValue({ meeting_id: "mtg_9" });
    render(<NewMeetingScreen agendaSources={SOURCES} />);
    await screen.findByRole("option", { name: "검색팀" });
    fireEvent.change(screen.getByPlaceholderText(/스프린트 킥오프/), {
      target: { value: "주간 회의" },
    });
    await waitFor(() => expect(on()).toBe(true));
    expect(screen.queryByText("검색 응답 시간 개선")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "지금 녹음 시작" }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/meetings/mtg_9/live"));
    const [payload] = create.mock.calls.at(-1) ?? [];
    expect(Object.keys(payload ?? {}).sort()).toEqual(["started_at", "team_id", "title"]);
    expect(JSON.stringify(payload)).not.toContain("검색 응답 시간 개선");
  });

  it("is not on a re-upload, which opens no meeting", () => {
    lines.mockClear();
    render(<NewMeetingScreen existingMeetingId="mtg_1" agendaSources={SOURCES} />);

    expect(screen.queryByText(/어젠다 초안/)).toBeNull();
    expect(lines).not.toHaveBeenCalled();
  });
});
