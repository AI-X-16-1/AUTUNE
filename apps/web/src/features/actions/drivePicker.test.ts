import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DRIVE_SCOPE,
  DrivePickError,
  downloadPicked,
  downloadPlan,
  drivePickerConfig,
  driveRefusal,
} from "./drivePicker";

// Choosing a file from Drive for the 자료 upload (#1369). What is under test:
// the button exists only where all three values are set, the one scope is
// `drive.file`, a Google editor is exported into a format the server reads,
// and no message carries a file's name. Names and ids are made up.

afterEach(() => {
  vi.unstubAllEnvs();
});

const setAll = () => {
  vi.stubEnv("NEXT_PUBLIC_GOOGLE_PICKER_CLIENT_ID", "client.apps.example");
  vi.stubEnv("NEXT_PUBLIC_GOOGLE_PICKER_API_KEY", "key-1");
  vi.stubEnv("NEXT_PUBLIC_GOOGLE_PICKER_APP_ID", "123456789");
};

describe("drivePickerConfig", () => {
  it("is null with nothing set", () => {
    vi.stubEnv("NEXT_PUBLIC_GOOGLE_PICKER_CLIENT_ID", "");
    vi.stubEnv("NEXT_PUBLIC_GOOGLE_PICKER_API_KEY", "");
    vi.stubEnv("NEXT_PUBLIC_GOOGLE_PICKER_APP_ID", "");
    expect(drivePickerConfig()).toBeNull();
  });

  it.each([
    ["NEXT_PUBLIC_GOOGLE_PICKER_CLIENT_ID"],
    ["NEXT_PUBLIC_GOOGLE_PICKER_API_KEY"],
    ["NEXT_PUBLIC_GOOGLE_PICKER_APP_ID"],
  ])("is null when only %s is missing", (missing) => {
    setAll();
    vi.stubEnv(missing, "  ");
    expect(drivePickerConfig()).toBeNull();
  });

  it("is the three values when all are set", () => {
    setAll();
    expect(drivePickerConfig()).toEqual({
      clientId: "client.apps.example",
      apiKey: "key-1",
      appId: "123456789",
    });
  });
});

describe("the scope", () => {
  it("is drive.file and nothing wider", () => {
    expect(DRIVE_SCOPE).toBe("https://www.googleapis.com/auth/drive.file");
  });
});

describe("downloadPlan", () => {
  it("downloads a stored file as it is", () => {
    const plan = downloadPlan({ id: "f1", name: "계획.pdf", mimeType: "application/pdf" });
    expect(plan.url).toBe("https://www.googleapis.com/drive/v3/files/f1?alt=media");
    expect(plan.fileName).toBe("계획.pdf");
  });

  it.each([
    ["application/vnd.google-apps.document", ".docx", "wordprocessingml.document"],
    ["application/vnd.google-apps.presentation", ".pptx", "presentationml.presentation"],
    ["application/vnd.google-apps.spreadsheet", ".xlsx", "spreadsheetml.sheet"],
  ])("exports %s as %s", (mimeType, suffix, mime) => {
    const plan = downloadPlan({ id: "g1", name: "분기 계획", mimeType });
    expect(plan.url).toContain("/g1/export?mimeType=");
    expect(decodeURIComponent(plan.url)).toContain(mime);
    expect(plan.fileName).toBe(`분기 계획${suffix}`);
  });

  it("does not add the ending twice", () => {
    const plan = downloadPlan({
      id: "g2",
      name: "계획.docx",
      mimeType: "application/vnd.google-apps.document",
    });
    expect(plan.fileName).toBe("계획.docx");
  });

  it("refuses a Google file that has no export we read", () => {
    expect(() =>
      downloadPlan({ id: "x", name: "설문", mimeType: "application/vnd.google-apps.form" }),
    ).toThrow(DrivePickError);
  });

  it("puts an id into the URL encoded", () => {
    const plan = downloadPlan({ id: "a/b?c", name: "n.pdf", mimeType: "application/pdf" });
    expect(plan.url).not.toContain("a/b?c");
    expect(plan.url).toContain(encodeURIComponent("a/b?c"));
  });
});

describe("downloadPicked", () => {
  const doc = { id: "f1", name: "비밀계획.pdf", mimeType: "application/pdf" };

  it("sends the token to Google only and returns a File", async () => {
    const fetcher = vi.fn(async () => new Response("abc", { status: 200 }));
    const file = await downloadPicked(doc, "tok", 100, fetcher as unknown as typeof fetch);
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url.startsWith("https://www.googleapis.com/")).toBe(true);
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
    expect(file.name).toBe("비밀계획.pdf");
    expect(file.size).toBe(3);
  });

  it("stops a file the rules would refuse before its body is read", async () => {
    const body = vi.fn();
    const response = new Response("x", { status: 200, headers: { "content-length": "999" } });
    response.arrayBuffer = body as unknown as () => Promise<ArrayBuffer>;
    const fetcher = vi.fn(async () => response);
    await expect(
      downloadPicked(doc, "tok", 100, fetcher as unknown as typeof fetch),
    ).rejects.toThrow(DrivePickError);
    expect(body).not.toHaveBeenCalled();
  });

  it("tells a refused file without naming it", async () => {
    const fetcher = vi.fn(async () => new Response("", { status: 404 }));
    const error = await downloadPicked(doc, "tok", 100, fetcher as unknown as typeof fetch).catch(
      (cause: unknown) => cause,
    );
    expect(error).toBeInstanceOf(DrivePickError);
    expect((error as Error).message).not.toContain("비밀계획");
    expect((error as Error).message).not.toContain("tok");
  });

  it("tells a network failure without the token", async () => {
    const fetcher = vi.fn(async () => {
      throw new Error("offline tok");
    });
    const error = await downloadPicked(doc, "tok", 100, fetcher as unknown as typeof fetch).catch(
      (cause: unknown) => cause,
    );
    expect((error as Error).message).not.toContain("tok");
  });
});

describe("driveRefusal", () => {
  it("shows our own messages and hides anything else's", () => {
    expect(driveRefusal(new DrivePickError("이유"))).toBe("이유");
    expect(driveRefusal(new Error("secret detail"))).not.toContain("secret");
  });
});
