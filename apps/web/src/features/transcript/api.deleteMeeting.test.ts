import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { deleteMeeting } from "./api";

// #1161: a member deletes a meeting. The title is typed by the person and goes
// in the body -- a title can name a client, and an address is what gets
// logged. The answer is a 204 with nothing in it.

const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>();

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe("deleteMeeting", () => {
  it("is a DELETE on the meeting, with the title in the body and not the address", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteMeeting("mtg/1", "고객사 A 주간 회의")).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(new URL(url, "http://autune.test").pathname).toBe("/api/audio/meetings/mtg%2F1");
    expect(url).not.toContain(encodeURIComponent("고객사"));
    expect(init?.method).toBe("DELETE");
    expect(new Headers(init?.headers).get("content-type")).toBe("application/json");
    expect(JSON.parse(String(init?.body))).toEqual({ title: "고객사 A 주간 회의" });
  });

  it("raises the server's refusal with its code", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ error: { code: "meeting_in_progress", message: "refused" } }),
        { status: 409 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const refused = await deleteMeeting("mtg_1", "회의").catch((caught: unknown) => caught);

    expect(refused).toBeInstanceOf(ApiError);
    expect((refused as ApiError).code).toBe("meeting_in_progress");
    expect((refused as ApiError).status).toBe(409);
  });
});
