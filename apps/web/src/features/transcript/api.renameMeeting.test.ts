import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { renameMeeting } from "./api";

// #1161: any member renames a meeting. The title is typed by a person and can
// name a client, so it goes in the body -- an address is what gets logged.

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

describe("renameMeeting", () => {
  it("is a PATCH on the meeting, with the title in the body and not the address", async () => {
    fetchMock.mockResolvedValue(answer(200, { meeting_id: "mtg/1", status: "complete" }));
    vi.stubGlobal("fetch", fetchMock);

    const state = await renameMeeting("mtg/1", "고객사 A 3분기 계획");

    expect(state).toEqual({ meeting_id: "mtg/1", status: "complete" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(new URL(url, "http://autune.test").pathname).toBe("/api/audio/meetings/mtg%2F1");
    expect(new URL(url, "http://autune.test").search).toBe("");
    expect(url).not.toContain(encodeURIComponent("고객사"));
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ title: "고객사 A 3분기 계획" });
  });

  it("raises the server's refusal with its details", async () => {
    fetchMock.mockResolvedValue(
      answer(422, {
        error: {
          code: "validation_error",
          message: "refused",
          details: { field: "title", reason: "personal_data", categories: ["phone"] },
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const refused = await renameMeeting("mtg_1", "x").catch((cause: unknown) => cause);

    expect(refused).toBeInstanceOf(ApiError);
    expect((refused as ApiError).status).toBe(422);
    expect((refused as ApiError).details).toEqual({
      field: "title",
      reason: "personal_data",
      categories: ["phone"],
    });
  });
});
