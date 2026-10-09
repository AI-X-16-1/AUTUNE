import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { searchMaterials } from "./api";

// Asking the team's uploaded materials a question (#817). The one thing the
// call has to get right is where the question travels: in the body, because
// an address is logged by everything it passes through and a question can
// name a person. And a failure is read without assuming our envelope: the
// two this call can meet are the framework's own.

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("searchMaterials", () => {
  it("posts the question in the body, and nothing of it in the address", async () => {
    const answer = { hits: [], notice: null, more: false };
    const fetched = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(
      async () =>
        new Response(JSON.stringify(answer), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetched);

    const got = await searchMaterials("team a", "김 대리 출시 일정");

    expect(got).toEqual(answer);
    expect(fetched).toHaveBeenCalledTimes(1);
    const [url, init] = fetched.mock.calls[0] ?? [];
    expect(url).toMatch(/\/api\/extraction\/materials\/search\?team_id=team%20a$/);
    expect(decodeURIComponent(String(url))).not.toContain("김 대리");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe("application/json");
    expect(JSON.parse(String(init?.body))).toEqual({ question: "김 대리 출시 일정" });
  });

  const failure = async (status: number, body: string): Promise<ApiError> => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(body, { status, statusText: "Refused" })),
    );
    try {
      await searchMaterials("team_a", "김 대리 출시 일정");
    } catch (cause) {
      if (cause instanceof ApiError) return cause;
      throw cause;
    }
    throw new Error("the search did not fail");
  };

  it("reads the framework's 422 as a refusal, and keeps the repeated question out of it", async () => {
    // What FastAPI answers to a body that fails its schema: no `error`, and
    // the value that was sent.
    const refused = await failure(
      422,
      JSON.stringify({
        detail: [{ type: "string_too_long", loc: ["body", "question"], input: "김 대리 출시 일정" }],
      }),
    );

    expect(refused.status).toBe(422);
    expect(refused.code).toBe("unknown");
    expect(JSON.stringify([refused.message, refused.details])).not.toContain("김 대리");
  });

  it("reads the route's own refusal of a question's length as the same 422", async () => {
    const refused = await failure(
      422,
      JSON.stringify({
        error: {
          code: "validation_error",
          message: "a question is 1 to 300 characters",
          details: { field: "question" },
        },
      }),
    );

    expect([refused.status, refused.code]).toEqual([422, "validation_error"]);
    expect(refused.details).toEqual({ field: "question" });
  });

  it("reads the bare 404 of a server with uploads off, and our own 404, apart", async () => {
    const off = await failure(404, JSON.stringify({ detail: "Not Found" }));
    expect([off.status, off.code]).toEqual([404, "unknown"]);

    const stranger = await failure(
      404,
      JSON.stringify({ error: { code: "not_found", message: "team not found", details: {} } }),
    );
    expect([stranger.status, stranger.code]).toEqual([404, "not_found"]);
  });

  it("reads a failure that is not JSON by its status", async () => {
    const down = await failure(502, "<html>bad gateway</html>");

    expect([down.status, down.code, down.message]).toEqual([502, "unknown", "Refused"]);
  });
});
