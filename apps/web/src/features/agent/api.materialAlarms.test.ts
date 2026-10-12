import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { acknowledgeMaterialAlarm, listMaterialAlarms } from "./api";

// #1201: the approvers' two calls about an upload that was stopped. The
// acknowledgement is answered 204, which the shared client would read as a
// body that does not parse.

afterEach(() => {
  vi.unstubAllGlobals();
});

type Fetch = (url: string, init?: RequestInit) => Promise<Response>;

describe("material alarms", () => {
  it("lists across the caller's teams, naming none", async () => {
    const rows = [
      {
        id: "mal_1",
        team_id: "team_1",
        marking: "korean_marking",
        created_at: "2026-10-09T03:05:00Z",
      },
    ];
    const fetched = vi.fn<Fetch>(
      async () =>
        new Response(JSON.stringify(rows), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetched);

    expect(await listMaterialAlarms()).toEqual(rows);

    const [url] = fetched.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith("/api/agent/material-alarms")).toBe(true);
  });

  it("acknowledges by POST with the row's team, and an empty 204 is a success", async () => {
    const fetched = vi.fn<Fetch>(
      async () => new Response(null, { status: 204 }),
    );
    vi.stubGlobal("fetch", fetched);

    await expect(
      acknowledgeMaterialAlarm("team 1", "mal 1"),
    ).resolves.toBeUndefined();

    const [url, init] = fetched.mock.calls[0] as [string, RequestInit];
    expect(
      url.endsWith(
        "/api/agent/material-alarms/mal%201/acknowledge?team_id=team%201",
      ),
    ).toBe(true);
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });

  it("fails with the server's refusal, which the screen tells apart by status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<Fetch>(
        async () =>
          new Response(
            JSON.stringify({
              error: { code: "not_found", message: "material alarm not found" },
            }),
            { status: 404, headers: { "content-type": "application/json" } },
          ),
      ),
    );

    const refusal = await acknowledgeMaterialAlarm("team_1", "mal_1").catch(
      (cause: unknown) => cause,
    );

    expect(refusal).toBeInstanceOf(ApiError);
    expect((refusal as ApiError).status).toBe(404);
  });
});
