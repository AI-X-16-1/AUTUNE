import { describe, expect, it } from "vitest";

import { drivePreviewUrl } from "../drive/driveLink";
import { contentSecurityPolicy, DRIVE_PREVIEW_ORIGINS } from "./contentSecurityPolicy";

/** The sources of one directive, or undefined when the policy has none. */
function sources(policy: string, name: string): string[] | undefined {
  const found = policy
    .split(";")
    .map((part) => part.trim().split(/\s+/))
    .find(([directive]) => directive === name);
  return found?.slice(1);
}

const built = contentSecurityPolicy({ development: false });

describe("contentSecurityPolicy", () => {
  it("loads scripts from the app's own origin and nowhere else", () => {
    expect(sources(built, "script-src")).toEqual(["'self'", "'unsafe-inline'"]);
    expect(sources(built, "default-src")).toEqual(["'self'"]);
  });

  it("adds 'unsafe-eval' in development only", () => {
    const dev = contentSecurityPolicy({ development: true });
    expect(sources(dev, "script-src")).toContain("'unsafe-eval'");
    expect(sources(built, "script-src")).not.toContain("'unsafe-eval'");
  });

  it("lets Google's file preview into a frame, and nothing else", () => {
    for (const kind of ["file", "document", "presentation", "spreadsheets"] as const) {
      const origin = new URL(drivePreviewUrl({ id: "abc", kind })).origin;
      expect(sources(built, "frame-src")).toContain(origin);
    }
    expect(sources(built, "frame-src")).toEqual(DRIVE_PREVIEW_ORIGINS);
  });

  it("connects to the live socket's own origin when a build names one", () => {
    expect(sources(built, "connect-src")).toEqual(["'self'"]);
    const live = contentSecurityPolicy({ development: false, liveUrl: "wss://api.example.com/x" });
    expect(sources(live, "connect-src")).toEqual(["'self'", "wss://api.example.com"]);
  });

  it("ignores a live URL that is blank or not a socket address", () => {
    for (const liveUrl of ["", "   ", "not a url", "https://api.example.com"]) {
      const policy = contentSecurityPolicy({ development: false, liveUrl });
      expect(sources(policy, "connect-src")).toEqual(["'self'"]);
    }
  });

  it("refuses plug-ins, foreign forms and being framed", () => {
    expect(sources(built, "object-src")).toEqual(["'none'"]);
    expect(sources(built, "form-action")).toEqual(["'self'"]);
    expect(sources(built, "frame-ancestors")).toEqual(["'none'"]);
    expect(sources(built, "base-uri")).toEqual(["'self'"]);
  });
});
