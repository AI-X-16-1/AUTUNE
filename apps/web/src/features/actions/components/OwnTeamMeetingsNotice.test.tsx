import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OwnTeamMeetingsNotice } from "./OwnTeamMeetingsNotice";
import type { CloudModelUse } from "../api";

// #392's operating rule, shown only on a server that sends meeting text out.

const get = vi.fn<() => Promise<CloudModelUse>>();
vi.mock("../api", () => ({ getCloudModelUse: () => get() }));

afterEach(() => {
  cleanup();
  get.mockReset();
});

describe("OwnTeamMeetingsNotice", () => {
  it("says the rule on the upload form in the words chosen for it", async () => {
    get.mockResolvedValue({ in_use: true });
    render(<OwnTeamMeetingsNotice entrance="upload" />);

    expect((await screen.findByRole("note")).textContent).toBe(
      "우리 팀 자신의 회의만 올려 주세요.",
    );
  });

  it("says it on the live gate as a recording, which is what starts there", async () => {
    get.mockResolvedValue({ in_use: true });
    render(<OwnTeamMeetingsNotice entrance="live" />);

    expect((await screen.findByRole("note")).textContent).toBe(
      "우리 팀 자신의 회의만 녹음해 주세요.",
    );
  });

  it("draws nothing on a server that sends nothing out", async () => {
    get.mockResolvedValue({ in_use: false });
    const { container } = render(<OwnTeamMeetingsNotice entrance="upload" />);

    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    await Promise.resolve();
    expect(container.innerHTML).toBe("");
  });

  it("draws nothing before the server has answered", () => {
    get.mockReturnValue(new Promise(() => undefined));
    const { container } = render(<OwnTeamMeetingsNotice entrance="live" />);

    expect(container.innerHTML).toBe("");
  });

  it("draws nothing, and raises nothing, when the question fails", async () => {
    get.mockRejectedValue(new Error("offline"));
    const { container } = render(<OwnTeamMeetingsNotice entrance="upload" />);

    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    await Promise.resolve();
    expect(container.innerHTML).toBe("");
  });

  it("takes the spacing its place gives it and adds none of its own", async () => {
    get.mockResolvedValue({ in_use: true });
    render(<OwnTeamMeetingsNotice entrance="live" className="mt-6" />);

    expect((await screen.findByRole("note")).className).toBe("mt-6");
  });
});
