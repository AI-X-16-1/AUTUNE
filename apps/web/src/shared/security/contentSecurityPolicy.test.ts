import { describe, expect, it } from "vitest";

import { drivePreviewUrl } from "../drive/driveLink";
import {
  contentSecurityPolicy,
  DRIVE_PREVIEW_ORIGINS,
  PICKER_CONNECT_SOURCES,
  PICKER_SCRIPT_SOURCES,
} from "./contentSecurityPolicy";

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

  it("opens for Google's Picker only in a build that turns it on", () => {
    // Off, and the flag left out, are the policy of a build without the feature.
    expect(contentSecurityPolicy({ development: false, drivePicker: false })).toBe(built);
    const on = contentSecurityPolicy({ development: false, drivePicker: true });
    expect(sources(on, "script-src")).toEqual(["'self'", "'unsafe-inline'", ...PICKER_SCRIPT_SOURCES]);
    expect(sources(on, "connect-src")).toEqual(["'self'", ...PICKER_CONNECT_SOURCES]);
    // The other directives do not move.
    for (const name of ["default-src", "style-src", "frame-src", "object-src", "frame-ancestors"]) {
      expect(sources(on, name)).toEqual(sources(built, name));
    }
  });

  it("names the Picker's origins as narrowly as the run showed", () => {
    const on = contentSecurityPolicy({ development: false, drivePicker: true });
    const all = [...(sources(on, "script-src") ?? []), ...(sources(on, "connect-src") ?? [])];
    // No scheme-only, wildcard or whole-googleapis.com source.
    expect(all.filter((source) => source.includes("*") || source === "https:")).toEqual([]);
    expect(sources(on, "connect-src")).not.toContain("https://www.googleapis.com");
    // The download URL the Picker code builds falls under the connect source.
    expect("https://www.googleapis.com/drive/v3/files/abc/export?mimeType=x").toMatch(
      new RegExp(`^${PICKER_CONNECT_SOURCES[0]}`),
    );
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
