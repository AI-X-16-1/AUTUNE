import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MaterialUploadRules } from "../types";
import { MaterialsScreen } from "./MaterialsScreen";

// Where "Drive에서 선택" appears on the 자료 screen (#1369): only where the
// deployment takes uploads AND all three Picker values were built in. The
// button is the only thing that brings Google's scripts in, so a screen
// without it has none.

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
  suffixes: [".txt", ".pdf", ".docx"],
  max_title_chars: 80,
  max_materials: 200,
};

const open = async (answer: MaterialUploadRules = RULES) => {
  session.mockResolvedValue({
    id: "user_me",
    email: "me@example.com",
    display_name: "Me",
    teams: [{ id: "team_a", name: "가 팀" }],
  });
  settingsGet.mockResolvedValue({ keep_until_deleted: false, retention_days: 45 });
  list.mockResolvedValue([]);
  rules.mockResolvedValue(answer);
  render(<MaterialsScreen />);
  await screen.findByRole("form", { name: "자료 등록" });
};

const setPicker = () => {
  vi.stubEnv("NEXT_PUBLIC_GOOGLE_PICKER_CLIENT_ID", "client.apps.example");
  vi.stubEnv("NEXT_PUBLIC_GOOGLE_PICKER_API_KEY", "key-1");
  vi.stubEnv("NEXT_PUBLIC_GOOGLE_PICKER_APP_ID", "123456789");
};

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  for (const fake of [session, list, rules, settingsGet]) fake.mockReset();
});

describe("Drive에서 선택 on the 자료 screen", () => {
  it("is not drawn without the Picker values", async () => {
    vi.stubEnv("NEXT_PUBLIC_GOOGLE_PICKER_CLIENT_ID", "");
    vi.stubEnv("NEXT_PUBLIC_GOOGLE_PICKER_API_KEY", "");
    vi.stubEnv("NEXT_PUBLIC_GOOGLE_PICKER_APP_ID", "");
    await open();
    await screen.findByRole("form", { name: "파일 올리기" });
    expect(screen.queryByRole("button", { name: "Drive에서 선택" })).toBeNull();
  });

  it("is drawn where the values are built in and uploads are on", async () => {
    setPicker();
    await open();
    expect(await screen.findByRole("button", { name: "Drive에서 선택" })).toBeTruthy();
  });

  it("is not drawn where uploads are off, whatever the values", async () => {
    setPicker();
    await open({ ...RULES, enabled: false });
    expect(screen.queryByRole("button", { name: "Drive에서 선택" })).toBeNull();
  });

  it("loads no Google script until the button is pressed", async () => {
    setPicker();
    await open();
    await screen.findByRole("button", { name: "Drive에서 선택" });
    const sources = Array.from(document.querySelectorAll("script")).map((tag) => tag.src);
    expect(sources.some((src) => src.includes("google.com"))).toBe(false);
  });
});
