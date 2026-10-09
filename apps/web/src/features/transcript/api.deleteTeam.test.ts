import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { deleteTeam } from "./api";

// #1007: the one person left on a team deletes it. The team's name is typed
// by the person and goes in the body -- a name can name a client, and an
// address is what gets logged.

const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>();

const answer = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe("deleteTeam", () => {
  it("is a DELETE on the team, with the name in the body and not the address", async () => {
    fetchMock.mockResolvedValue(answer(200, [{ team_id: "team_2", name: "Beta" }]));
    vi.stubGlobal("fetch", fetchMock);

    const left = await deleteTeam("team/1", "고객사 A 프로젝트");

    expect(left).toEqual([{ team_id: "team_2", name: "Beta" }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(new URL(url, "http://autune.test").pathname).toBe("/api/audio/teams/team%2F1");
    expect(url).not.toContain(encodeURIComponent("고객사"));
    expect(init?.method).toBe("DELETE");
    expect(JSON.parse(String(init?.body))).toEqual({ name: "고객사 A 프로젝트" });
  });

  it("raises the server's refusal with its code", async () => {
    fetchMock.mockResolvedValue(
      answer(409, { error: { code: "team_meeting_in_progress", message: "refused" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const refused = await deleteTeam("team_1", "Alpha").catch((caught: unknown) => caught);

    expect(refused).toBeInstanceOf(ApiError);
    expect((refused as ApiError).code).toBe("team_meeting_in_progress");
    expect((refused as ApiError).status).toBe(409);
  });
});
