import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { getMaterialUploadRules, uploadMaterial } from "./api";

// Sending a file for the team's shelf (#817). The body is a form of exactly
// two parts, so it cannot go through the shared client, which labels every
// body JSON; and a refusal has to come back as the same `ApiError` the rest
// of the feature reads. The file here is two made-up words.

afterEach(() => {
  vi.unstubAllGlobals();
});

const answer = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

const file = () => new File(["made-up words"], "분기 계획.pdf", { type: "application/pdf" });

const failure = async (response: Response): Promise<ApiError> => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response),
  );
  try {
    await uploadMaterial("team a", { title: "분기 계획", file: file() });
  } catch (cause) {
    if (cause instanceof ApiError) return cause;
    throw cause;
  }
  throw new Error("the upload did not fail");
};

describe("uploadMaterial", () => {
  it("posts a form of a title and a file to the team's upload, and returns the row", async () => {
    const row = { id: "mat_9", source: "upload", title: "분기 계획" };
    const fetched = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () =>
      answer(201, row),
    );
    vi.stubGlobal("fetch", fetched);

    const saved = await uploadMaterial("team a", { title: "분기 계획", file: file() });

    expect(fetched).toHaveBeenCalledTimes(1);
    const [url, init] = fetched.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith("/api/extraction/materials/upload?team_id=team%20a")).toBe(true);
    expect(init.method).toBe("POST");
    const form = init.body as FormData;
    expect([...form.keys()]).toEqual(["title", "file"]);
    expect(form.get("title")).toBe("분기 계획");
    expect((form.get("file") as File).name).toBe("분기 계획.pdf");
    expect(saved).toEqual(row);
  });

  it("leaves the content type to the browser, which has to write the boundary", async () => {
    const fetched = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () =>
      answer(201, {}),
    );
    vi.stubGlobal("fetch", fetched);

    await uploadMaterial("team_a", { title: "분기 계획", file: file() });

    const [, init] = fetched.mock.calls[0] as [string, RequestInit];
    const names = Object.keys(init.headers ?? {}).map((name) => name.toLowerCase());
    expect(names).not.toContain("content-type");
  });

  it("fails with the server's refusal: its code, its message and its details", async () => {
    const cause = await failure(
      answer(422, {
        error: {
          code: "validation_error",
          message: "the file cannot be read: protected",
          details: { field: "file" },
        },
      }),
    );

    expect(cause.status).toBe(422);
    expect(cause.code).toBe("validation_error");
    expect(cause.message).toBe("the file cannot be read: protected");
    expect(cause.details).toEqual({ field: "file" });
  });

  it("reads a refusal that has no details", async () => {
    const cause = await failure(
      answer(422, {
        error: {
          code: "confidential_file",
          message: "the file is marked confidential and was not taken",
        },
      }),
    );

    expect(cause.code).toBe("confidential_file");
    expect(cause.details).toEqual({});
  });

  it("reads the framework's own 404, which has no error in it, as a 404 with no code of ours", async () => {
    const cause = await failure(answer(404, { detail: "Not Found" }));

    expect(cause.status).toBe(404);
    expect(cause.code).toBe("unknown");
  });

  it("reads an answer that is not JSON by its status", async () => {
    const cause = await failure(
      new Response("<html>Bad Gateway</html>", { status: 502, statusText: "Bad Gateway" }),
    );

    expect(cause.status).toBe(502);
    expect(cause.code).toBe("unknown");
    expect(cause.message).toBe("Bad Gateway");
  });
});

describe("getMaterialUploadRules", () => {
  it("asks for the team's rules", async () => {
    const rules = { enabled: false, max_bytes: 1, suffixes: [], max_title_chars: 120, max_materials: 200 };
    const fetched = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () =>
      answer(200, rules),
    );
    vi.stubGlobal("fetch", fetched);

    expect(await getMaterialUploadRules("team a")).toEqual(rules);

    const [url] = fetched.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith("/api/extraction/materials/upload-rules?team_id=team%20a")).toBe(true);
  });
});
