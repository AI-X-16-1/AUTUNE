import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MaterialUploadRules } from "../types";
import { MaterialsScreen } from "./MaterialsScreen";

// The page's notes can be folded: the page's own paragraph and the notes under
// the upload form go together, both are open on every visit, and nothing is
// written to the browser for the choice.

const session = vi.fn();
const list = vi.fn();
const rules = vi.fn();
const settingsGet = vi.fn();
vi.mock("@/shared/api/auth", () => ({ getSession: () => session() }));
vi.mock("../api", () => ({
  getMaterialUploadRules: (teamId: string) => rules(teamId),
  listMaterials: (teamId: string) => list(teamId),
  registerMaterial: vi.fn(),
  uploadMaterial: vi.fn(),
  deleteMaterial: vi.fn(),
  getMaterialSettings: (teamId: string) => settingsGet(teamId),
  saveMaterialSettings: vi.fn(),
}));

const RULES: MaterialUploadRules = {
  enabled: true,
  max_bytes: 5 * 1024 * 1024,
  suffixes: [".txt", ".pdf"],
  max_title_chars: 80,
  max_materials: 200,
};

const open = async () => {
  session.mockResolvedValue({
    id: "user_me",
    email: "me@example.com",
    display_name: "Me",
    teams: [{ id: "team_a", name: "가 팀" }],
  });
  settingsGet.mockResolvedValue({ keep_until_deleted: false, retention_days: 45 });
  list.mockResolvedValue([]);
  rules.mockResolvedValue(RULES);
  render(<MaterialsScreen />);
  await screen.findByRole("form", { name: "파일 올리기" });
};

afterEach(() => {
  cleanup();
  for (const fake of [session, list, rules, settingsGet]) fake.mockReset();
});

describe("주의사항 접기", () => {
  it("shows every note by default", async () => {
    await open();
    expect(screen.getByText(/팀이 함께 보는 자료를 모아 둡니다/).hidden).toBe(false);
    expect(screen.getByText(/원본은 보관하지 않으며/).hidden).toBe(false);
    const toggle = screen.getByRole("button", { name: "주의사항 접기" });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
  });

  it("hides the page's note and the form's note together, and brings them back", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "주의사항 접기" }));
    expect(screen.getByText(/팀이 함께 보는 자료를 모아 둡니다/).hidden).toBe(true);
    expect(screen.getByText(/원본은 보관하지 않으며/).hidden).toBe(true);
    const toggle = screen.getByRole("button", { name: "주의사항 펼치기" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(screen.getByText(/원본은 보관하지 않으며/).hidden).toBe(false);
  });

  it("writes nothing to the browser's storage", async () => {
    const set = vi.spyOn(Storage.prototype, "setItem");
    await open();
    fireEvent.click(screen.getByRole("button", { name: "주의사항 접기" }));
    expect(set).not.toHaveBeenCalled();
    set.mockRestore();
  });
});
