import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import * as api from "../api";
import { StoredTranscript } from "./StoredTranscript";

// The speaker list is its own fetch with its own tests; here it answers empty.
vi.mock("../hooks/useSpeakers", () => ({
  useSpeakers: () => ({
    speakers: [],
    speakersError: null,
    members: [],
    membersError: null,
    assign: vi.fn(),
    name: vi.fn(),
    unassign: vi.fn(),
    assignError: null,
    pending: false,
  }),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// #1176: a transcript read that 500s showed "Internal Server Error" as it came.
describe("StoredTranscript, a read that fails", () => {
  it("says so in Korean and reads the transcript again on 다시 시도", async () => {
    const read = vi
      .spyOn(api, "getTranscript")
      .mockRejectedValueOnce(new ApiError(500, "unknown", "Internal Server Error"))
      .mockResolvedValueOnce([]);

    render(<StoredTranscript meetingId="mtg_1" teamId="team_1" />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("전사를 불러오지 못했습니다.");
    expect(alert.textContent).not.toContain("Internal Server Error");

    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));

    expect(await screen.findByText(/아직 전사된 내용이 없습니다/)).toBeTruthy();
    expect(read).toHaveBeenCalledTimes(2);
  });
});
