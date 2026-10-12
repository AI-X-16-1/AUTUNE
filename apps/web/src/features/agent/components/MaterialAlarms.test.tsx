import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import type { MaterialAlarm } from "../types";
import { MaterialAlarms } from "./MaterialAlarms";

// #817, #1201: the approvers' list of uploads that were stopped for a
// confidentiality marking, behind the link their DM carries.

const api = vi.hoisted(() => ({
  listMaterialAlarms: vi.fn(),
  acknowledgeMaterialAlarm: vi.fn(),
}));
vi.mock("../api", () => api);

const KOREAN: MaterialAlarm = {
  id: "mal_1",
  team_id: "team_1",
  marking: "korean_marking",
  created_at: "2026-10-09T03:05:00Z",
};
const ENGLISH: MaterialAlarm = {
  id: "mal_2",
  team_id: "team_2",
  marking: "english_marking",
  created_at: "2026-10-09T04:10:00Z",
};

beforeEach(() => {
  api.listMaterialAlarms.mockReset();
  api.acknowledgeMaterialAlarm.mockReset();
});
afterEach(cleanup);

describe("MaterialAlarms", () => {
  it("draws nothing for someone the server answers an empty list", async () => {
    api.listMaterialAlarms.mockResolvedValue([]);

    const { container } = render(<MaterialAlarms />);

    await waitFor(() => expect(api.listMaterialAlarms).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });

  it("draws nothing when the list cannot be read", async () => {
    api.listMaterialAlarms.mockRejectedValue(new ApiError(500, "unknown", "x"));

    const { container } = render(<MaterialAlarms />);

    await waitFor(() => expect(api.listMaterialAlarms).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });

  it("shows when and which kind, and says what was not kept", async () => {
    api.listMaterialAlarms.mockResolvedValue([KOREAN, ENGLISH]);

    render(<MaterialAlarms />);

    expect(
      await screen.findByRole("heading", { name: "받지 않은 자료 업로드" }),
    ).toBeTruthy();
    const rows = screen
      .getAllByRole("listitem")
      .map((row) => row.textContent ?? "");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toContain("한글 표시");
    expect(rows[1]).toContain("영문 표시");
    // The time is the reader's own clock's, so only its day is asserted.
    expect(rows[0]).toContain("10월 9일");
    const words = document.body.textContent?.split(/\s+/).join(" ") ?? "";
    expect(words).toContain("올린 사람은 저장되지 않았습니다");
    expect(words).toContain("누가 확인했는지는 기록되지 않습니다");
    // An id is the server's handle, not something to read.
    expect(words).not.toContain("mal_1");
  });

  it("acknowledges with the row's own team and takes the row away", async () => {
    api.listMaterialAlarms.mockResolvedValue([KOREAN, ENGLISH]);
    api.acknowledgeMaterialAlarm.mockResolvedValue(undefined);
    render(<MaterialAlarms />);
    const buttons = await screen.findAllByRole("button", { name: "확인" });

    fireEvent.click(buttons[1]!);

    expect(api.acknowledgeMaterialAlarm).toHaveBeenCalledWith(
      "team_2",
      "mal_2",
    );
    await waitFor(() =>
      expect(screen.getAllByRole("listitem")).toHaveLength(1),
    );
    expect(screen.getByRole("listitem").textContent).toContain("한글 표시");
  });

  it("takes the row away when another approver was first", async () => {
    api.listMaterialAlarms.mockResolvedValue([KOREAN]);
    api.acknowledgeMaterialAlarm.mockRejectedValue(
      new ApiError(404, "not_found", "material alarm mal_1 not found"),
    );
    const { container } = render(<MaterialAlarms />);

    fireEvent.click(await screen.findByRole("button", { name: "확인" }));

    await waitFor(() => expect(container.innerHTML).toBe(""));
  });

  it("keeps the row and says so when the acknowledgement fails", async () => {
    api.listMaterialAlarms.mockResolvedValue([KOREAN]);
    api.acknowledgeMaterialAlarm.mockRejectedValue(
      new ApiError(500, "unknown", "material alarm mal_1 could not be deleted"),
    );
    render(<MaterialAlarms />);

    fireEvent.click(await screen.findByRole("button", { name: "확인" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(
      "확인하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    );
    // Never the API's own message: it is English and carries the id.
    expect(document.body.textContent).not.toContain("mal_1");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });
});
