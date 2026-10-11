import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NewMeetingScreen } from "./NewMeetingScreen";

// S06's first option row, as the small cut of S14 (#1147): offered on the live
// path, with an end time that is kept in the tab and sent nowhere.

const push = vi.fn<(href: string) => void>();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

type Created = { title: string; team_id: string; started_at?: string };
const create = vi.fn<(payload: Created) => Promise<{ meeting_id: string }>>();
vi.mock("../api", () => ({
  attestConsent: vi.fn(),
  createMeeting: (payload: Created) => create(payload),
  listTeams: () => Promise.resolve([{ team_id: "team_1", name: "검색팀", pinned: false }]),
  uploadRecording: vi.fn(),
}));

afterEach(() => {
  cleanup();
  push.mockReset();
  create.mockReset();
  window.sessionStorage.clear();
});

const ROW = /종료 5분 전 미해결 갭 알림/;
const row = () => screen.getByRole("checkbox", { name: ROW }) as HTMLInputElement;
const endTime = () => screen.queryByLabelText(/종료 예정 시각/) as HTMLInputElement | null;

/** The form with a title and its one team, ready to open a live meeting. */
async function ready() {
  const view = render(<NewMeetingScreen />);
  await screen.findByRole("option", { name: "검색팀" });
  fireEvent.change(screen.getByPlaceholderText(/스프린트 킥오프/), {
    target: { value: "주간 회의" },
  });
  return view;
}

const goLive = () => fireEvent.click(screen.getByRole("button", { name: "지금 녹음 시작" }));

describe("NewMeetingScreen, the end-of-meeting alert row", () => {
  it("can be ticked on the live path, and asks for the end time only once it is", async () => {
    await ready();

    expect(row().disabled).toBe(false);
    expect(row().checked).toBe(false);
    expect(endTime()).toBeNull();

    fireEvent.click(row());

    expect(row().checked).toBe(true);
    expect(endTime()).not.toBeNull();
  });

  it("says what the alert is and what it is not, in the row itself", async () => {
    await ready();

    const label = row().closest("label")!;
    expect(label.textContent).toContain("이전 회의에서 닫지 못한 갭");
    expect(label.textContent).toContain("이 회의에서 결정되지 않은 것을 찾지는 않습니다");
    // S06's wording would promise this meeting's undecided items.
    expect(label.textContent).not.toContain("미결정 사항");
  });

  it("keeps the planned end in the tab for the meeting it just opened, and sends none of it", async () => {
    create.mockResolvedValue({ meeting_id: "mtg_9" });
    await ready();
    fireEvent.click(row());
    fireEvent.change(endTime()!, { target: { value: "23:59" } });

    goLive();

    await waitFor(() => expect(push).toHaveBeenCalledWith("/meetings/mtg_9/live"));
    const kept = window.sessionStorage.getItem("autune.plannedEnd.mtg_9");
    expect(kept).not.toBeNull();
    const at = new Date(kept!);
    expect([at.getHours(), at.getMinutes()]).toEqual([23, 59]);
    expect(at.getTime()).toBeGreaterThan(Date.now());
    // The create payload has no field for an end, and none is invented.
    const [payload] = create.mock.calls.at(-1) ?? [];
    expect(Object.keys(payload ?? {}).sort()).toEqual([
      "started_at",
      "team_id",
      "title",
    ]);
  });

  it("opens no meeting when the row is ticked with no time, and says why", async () => {
    await ready();
    fireEvent.click(row());

    goLive();

    expect((await screen.findByRole("alert")).textContent).toContain("종료 예정 시각을 입력");
    expect(create).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    expect(window.sessionStorage.length).toBe(0);
  });

  it("keeps nothing when the row is left unticked", async () => {
    create.mockResolvedValue({ meeting_id: "mtg_9" });
    await ready();

    goLive();

    await waitFor(() => expect(push).toHaveBeenCalledWith("/meetings/mtg_9/live"));
    expect(window.sessionStorage.length).toBe(0);
  });

  it("keeps nothing for a meeting saved for later, and the field says so beforehand", async () => {
    create.mockResolvedValue({ meeting_id: "mtg_9" });
    await ready();
    fireEvent.click(row());
    fireEvent.change(endTime()!, { target: { value: "23:59" } });

    expect(endTime()!.closest("div")!.textContent).toContain("“저장만”으로 연 회의에는");
    fireEvent.click(screen.getByRole("button", { name: "저장만" }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/meetings/mtg_9"));
    expect(window.sessionStorage.length).toBe(0);
  });

  it("is drawn disabled under the file source, with the reason", async () => {
    await ready();
    fireEvent.click(screen.getByRole("radio", { name: /녹음 파일 업로드/ }));

    expect(row().disabled).toBe(true);
    expect(row().closest("label")!.textContent).toContain(
      "실시간 녹음을 지금 시작할 때만 켤 수 있습니다",
    );
    expect(endTime()).toBeNull();
  });

  it("leaves the agenda row as it was: disabled, Phase 2", async () => {
    await ready();

    const agenda = screen.getByRole("checkbox", {
      name: /자료 연결 후 어젠다 자동 생성/,
    }) as HTMLInputElement;
    expect(agenda.disabled).toBe(true);
    expect(agenda.closest("label")!.textContent).toContain("Phase 2");
  });
});
