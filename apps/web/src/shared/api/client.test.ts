import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "./client";

// Every screen tells failures apart by `ApiError`'s status and code. A failed
// answer is one whatever its body holds: not every failure is written by our
// own handlers.

const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>();

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

async function failed(status: number, body: string): Promise<unknown> {
  fetchMock.mockResolvedValue(new Response(body, { status, statusText: "Refused" }));
  vi.stubGlobal("fetch", fetchMock);
  return api.audio("/meetings/mtg_1").catch((cause: unknown) => cause);
}

const read = (cause: unknown) => {
  expect(cause).toBeInstanceOf(ApiError);
  const error = cause as ApiError;
  return [error.status, error.code, error.message, error.details];
};

describe("request, a failed answer", () => {
  it("reads our own envelope whole", async () => {
    const cause = await failed(
      422,
      JSON.stringify({
        error: {
          code: "validation_error",
          message: "a title is at least one character",
          details: { field: "title" },
        },
      }),
    );

    expect(read(cause)).toEqual([
      422,
      "validation_error",
      "a title is at least one character",
      { field: "title" },
    ]);
  });

  it("reads our envelope without details", async () => {
    const cause = await failed(
      409,
      JSON.stringify({ error: { code: "nothing_to_cancel", message: "nothing is running" } }),
    );

    expect(read(cause)).toEqual([409, "nothing_to_cancel", "nothing is running", {}]);
  });

  it("reads the framework's 404 for a route that is not mounted by its status", async () => {
    const cause = await failed(404, JSON.stringify({ detail: "Not Found" }));

    expect(read(cause)).toEqual([404, "unknown", "Refused", {}]);
  });

  it("reads the framework's 422 by its status, and keeps what it repeats out of the error", async () => {
    const cause = await failed(
      422,
      JSON.stringify({
        detail: [
          {
            type: "string_too_long",
            loc: ["body", "title"],
            msg: "String should have at most 400 characters",
            input: "김 대리 010-1234-5678 통화",
          },
        ],
      }),
    );

    expect(read(cause)).toEqual([422, "unknown", "Refused", {}]);
    expect(JSON.stringify(read(cause))).not.toContain("010");
  });

  it.each([
    ["nothing", "null"],
    ["a string", '"Bad Gateway"'],
    ["a list", "[1, 2]"],
    ["an error that is a string", '{"error": "Bad Gateway"}'],
    ["an error that is null", '{"error": null}'],
    ["an error that is a list", '{"error": ["x"]}'],
  ])("reads a JSON body that is %s by its status", async (_what, body) => {
    expect(read(await failed(502, body))).toEqual([502, "unknown", "Refused", {}]);
  });

  it("takes each field only when it is what the error says it is", async () => {
    const cause = await failed(
      500,
      JSON.stringify({ error: { code: 7, message: { text: "x" }, details: "none" } }),
    );

    expect(read(cause)).toEqual([500, "unknown", "Refused", {}]);
  });

  it("reads details that are null as none", async () => {
    const cause = await failed(
      409,
      JSON.stringify({ error: { code: "conflict", message: "no", details: null } }),
    );

    expect(read(cause)).toEqual([409, "conflict", "no", {}]);
  });

  it("does not take details that are a list", async () => {
    const cause = await failed(
      422,
      JSON.stringify({ error: { code: "validation_error", message: "no", details: ["title"] } }),
    );

    expect(read(cause)).toEqual([422, "validation_error", "no", {}]);
  });

  it("reads a body that is not JSON by its status", async () => {
    expect(read(await failed(502, "<html>bad gateway</html>"))).toEqual([
      502,
      "unknown",
      "Refused",
      {},
    ]);
  });
});

describe("request, an answer that went through", () => {
  it("is the body, parsed", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ meeting_id: "mtg_1" })));
    vi.stubGlobal("fetch", fetchMock);

    expect(await api.audio("/meetings/mtg_1")).toEqual({ meeting_id: "mtg_1" });
  });
});
