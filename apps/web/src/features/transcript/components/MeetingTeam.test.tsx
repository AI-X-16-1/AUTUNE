import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { onTeamChosen } from "../selectedTeam";
import type { MeetingDetail } from "../types";
import { MeetingTeam } from "./MeetingTeam";

// Opening a meeting makes its team the one being looked at (the user,
// 2026-10-13): a second team's meeting opened from 승인 대기 sat under the
// first team's name in the sidebar.

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

const meeting = (teamId: string) => ({ id: "mtg_1", team_id: teamId }) as unknown as MeetingDetail;

describe("MeetingTeam", () => {
  it("chooses the meeting's team when another was chosen", async () => {
    window.localStorage.setItem("autune.team", "team_1");
    vi.spyOn(api, "getMeeting").mockResolvedValue(meeting("team_2"));
    const heard = vi.fn();
    const stop = onTeamChosen(heard);

    render(<MeetingTeam meetingId="mtg_1" />);

    await waitFor(() => expect(heard).toHaveBeenCalledWith("team_2"));
    expect(window.localStorage.getItem("autune.team")).toBe("team_2");
    stop();
  });

  it("announces nothing when the meeting's team is already chosen", async () => {
    window.localStorage.setItem("autune.team", "team_1");
    const read = vi.spyOn(api, "getMeeting").mockResolvedValue(meeting("team_1"));
    const heard = vi.fn();
    const stop = onTeamChosen(heard);

    render(<MeetingTeam meetingId="mtg_1" />);

    await waitFor(() => expect(read).toHaveBeenCalled());
    expect(heard).not.toHaveBeenCalled();
    stop();
  });

  it("leaves the choice alone when the meeting cannot be read", async () => {
    window.localStorage.setItem("autune.team", "team_1");
    const read = vi.spyOn(api, "getMeeting").mockRejectedValue(new Error("404"));

    render(<MeetingTeam meetingId="mtg_1" />);

    await waitFor(() => expect(read).toHaveBeenCalled());
    expect(window.localStorage.getItem("autune.team")).toBe("team_1");
  });
});
