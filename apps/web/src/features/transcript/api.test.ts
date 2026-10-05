import { afterEach, describe, expect, it, vi } from "vitest";

import { listMeetings } from "./api";

// The one thing the home screen's team row changes on the wire: which
// meetings are asked for.

const audio = vi.fn<(path: string) => Promise<unknown>>(() => Promise.resolve([]));
vi.mock("@/shared/api/client", async (original) => ({
  ...(await original<typeof import("@/shared/api/client")>()),
  api: { audio: (path: string) => audio(path) },
}));

afterEach(() => {
  audio.mockClear();
});

describe("listMeetings", () => {
  it("names the team it wants the meetings of", async () => {
    await listMeetings("team_search");

    expect(audio).toHaveBeenCalledExactlyOnceWith("/meetings?team_id=team_search");
  });

  it("sends a team id as a value, whatever is in it", async () => {
    await listMeetings("a&b=c d");

    expect(audio).toHaveBeenCalledExactlyOnceWith("/meetings?team_id=a%26b%3Dc%20d");
  });

  it("asks for every team's when no team is named", async () => {
    await listMeetings();

    expect(audio).toHaveBeenCalledExactlyOnceWith("/meetings");
  });
});
