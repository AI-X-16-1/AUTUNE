import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { closeActionItem } from "./api";

// Closing an item without finishing it is its own call (#856), not a PATCH of
// the status: the server keeps a different event for it, and a status edit
// would leave the item looking like finished work.

afterEach(() => {
  vi.unstubAllGlobals();
});

const answer = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

describe("closeActionItem", () => {
  it("posts to the item's close, with no body, and returns the item the server answered", async () => {
    const closed = { id: "act 1", status: "done", closed_unfinished: true };
    const fetched = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () =>
      answer(200, closed),
    );
    vi.stubGlobal("fetch", fetched);

    const item = await closeActionItem("act 1");

    expect(fetched).toHaveBeenCalledTimes(1);
    const [url, init] = fetched.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith("/api/extraction/action-items/act%201/close")).toBe(true);
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect(item).toEqual(closed);
  });

  it("fails with the server's refusal for an item that is not open", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        answer(409, {
          error: { code: "conflict", message: "this item is already closed", details: {} },
        }),
      ),
    );

    await expect(closeActionItem("act_1")).rejects.toBeInstanceOf(ApiError);
  });
});
