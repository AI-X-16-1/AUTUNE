import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { ContextTab } from "./ContextTab";
import type { CarriedLine, CarriedSource } from "../hooks/useCarriedAgenda";
import type { BriefRead } from "../types";

// Where the tab gets the agenda draft's carried entries (#1147): the route's
// sources, asked about the earlier meeting the brief itself chose.

const getBrief = vi.fn<(meetingId: string) => Promise<BriefRead>>();
vi.mock("../api", () => ({ getBrief: (meetingId: string) => getBrief(meetingId) }));
vi.mock("../hooks/useTopicLinks", () => ({
  useTopicLinks: () => ({ asserted: [], pending: [], loading: false, error: null, decide: vi.fn() }),
}));

afterEach(() => {
  cleanup();
  getBrief.mockReset();
});

const brief = (meetingId: string, earlier: string | null): BriefRead => ({
  meeting_id: meetingId,
  title: "주간 회의",
  starts_at: null,
  recap:
    earlier === null
      ? null
      : { meeting_id: earlier, title: "지난 주간 회의", day: "2026-10-05", topics: [], decisions: [] },
  recap_gone: false,
  match_reason: earlier === null ? null : "series",
  agenda: [],
  sent_at: null,
});

const source = (label: string, lines: CarriedLine[] | Error) => ({
  label,
  lines: vi.fn<CarriedSource["lines"]>(() =>
    lines instanceof Error ? Promise.reject(lines) : Promise.resolve(lines),
  ),
});
/** A source whose answers the test lets go of, per meeting asked. */
function held(label: string) {
  const release: Record<string, (lines: CarriedLine[]) => void> = {};
  const lines = vi.fn<CarriedSource["lines"]>(
    (earlier) =>
      new Promise<readonly CarriedLine[]>((resolve) => {
        release[earlier] = resolve;
      }),
  );
  return { source: { label, lines }, release };
}

const draft = () => screen.queryByRole("region", { name: "어젠다 초안" });

describe("ContextTab, the agenda draft's carried entries", () => {
  it("asks each source once, about the earlier meeting the brief names — not about this one", async () => {
    getBrief.mockResolvedValue(brief("mtg_next", "mtg_old"));
    const gaps = source("지난 회의의 미해결 갭", [{ title: "롤백 계획이 없습니다" }]);
    const sources = [gaps];

    render(<ContextTab meetingId="mtg_next" agendaSources={sources} />);

    expect(await screen.findByText("롤백 계획이 없습니다")).toBeTruthy();
    expect(gaps.lines).toHaveBeenCalledTimes(1);
    expect(gaps.lines).toHaveBeenCalledWith("mtg_old");
  });

  it("asks nothing when the brief has no earlier meeting, and still draws the draft section", async () => {
    getBrief.mockResolvedValue(brief("mtg_next", null));
    const gaps = source("지난 회의의 미해결 갭", [{ title: "롤백 계획이 없습니다" }]);

    render(<ContextTab meetingId="mtg_next" agendaSources={[gaps]} />);

    await waitFor(() => expect(draft()).not.toBeNull());
    expect(gaps.lines).not.toHaveBeenCalled();
    expect(draft()!.textContent).toContain("초안에 넣을 것이 아직 없습니다.");
  });

  it("asks nothing for a meeting with no brief, and draws nothing", async () => {
    getBrief.mockRejectedValue(new ApiError(404, "not_found", "no brief"));
    const gaps = source("지난 회의의 미해결 갭", [{ title: "롤백 계획이 없습니다" }]);

    render(<ContextTab meetingId="mtg_done" agendaSources={[gaps]} />);

    await waitFor(() => expect(getBrief).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(gaps.lines).not.toHaveBeenCalled();
    expect(draft()).toBeNull();
  });

  it("leaves out a source that cannot be read and raises no error over the brief", async () => {
    getBrief.mockResolvedValue(brief("mtg_next", "mtg_old"));
    const sources = [
      source("지난 회의의 미해결 갭", new Error("boom")),
      source("끝나지 않은 일", [{ title: "부하 테스트" }]),
    ];

    render(<ContextTab meetingId="mtg_next" agendaSources={sources} />);

    expect(await screen.findByText("부하 테스트")).toBeTruthy();
    expect(draft()!.textContent).not.toContain("지난 회의의 미해결 갭");
    expect(screen.queryByText("브리프를 불러오지 못했습니다.")).toBeNull();
    expect(document.body.textContent).not.toContain("boom");
  });

  it("is the panel it was when the route supplies no source", async () => {
    getBrief.mockResolvedValue(brief("mtg_next", "mtg_old"));

    render(<ContextTab meetingId="mtg_next" />);

    expect(await screen.findByText("이번 회의에 연결된 안건이 없습니다.")).toBeTruthy();
    expect(draft()).toBeNull();
  });

  it("does not show one meeting's carried entries under another meeting's brief", async () => {
    getBrief.mockImplementation((meetingId) =>
      Promise.resolve(meetingId === "mtg_a" ? brief("mtg_a", "old_a") : brief("mtg_b", "old_b")),
    );
    const gaps = held("지난 회의의 미해결 갭");
    const sources = [gaps.source];
    const view = render(<ContextTab meetingId="mtg_a" agendaSources={sources} />);
    await waitFor(() => expect(gaps.source.lines).toHaveBeenCalledWith("old_a"));

    view.rerender(<ContextTab meetingId="mtg_b" agendaSources={sources} />);
    await waitFor(() => expect(gaps.source.lines).toHaveBeenCalledWith("old_b"));
    // The first meeting's answer lands late, while the second's is still out.
    gaps.release.old_a?.([{ title: "A 회의의 갭" }]);
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(screen.queryByText("A 회의의 갭")).toBeNull();

    gaps.release.old_b?.([{ title: "B 회의의 갭" }]);
    expect(await screen.findByText("B 회의의 갭")).toBeTruthy();
    expect(screen.queryByText("A 회의의 갭")).toBeNull();
  });

  it("drops what it held for the earlier brief as soon as the brief changes", async () => {
    getBrief.mockImplementation((meetingId) =>
      Promise.resolve(meetingId === "mtg_a" ? brief("mtg_a", "old_a") : brief("mtg_b", "old_b")),
    );
    const lines = vi.fn<CarriedSource["lines"]>((earlier) =>
      earlier === "old_a" ? Promise.resolve([{ title: "A 회의의 갭" }]) : new Promise(() => undefined),
    );
    const sources = [{ label: "지난 회의의 미해결 갭", lines }];
    const view = render(<ContextTab meetingId="mtg_a" agendaSources={sources} />);
    expect(await screen.findByText("A 회의의 갭")).toBeTruthy();

    view.rerender(<ContextTab meetingId="mtg_b" agendaSources={sources} />);

    await waitFor(() => expect(lines).toHaveBeenCalledWith("old_b"));
    expect(screen.queryByText("A 회의의 갭")).toBeNull();
  });
});
