import { afterEach, describe, expect, it, vi } from "vitest";

import type { IntegrationScope } from "@/shared/api/auth";
import { ApiError } from "@/shared/api/client";

import { MATERIALS_AGENDA, MATERIALS_BRIEF_AGENDA, agendaQuestion } from "./materialAgenda";
import type { Material, MaterialSearchAnswer, MaterialUploadRules } from "./types";

// The team's uploaded materials as a source of the agenda draft (#1147 B2):
// counted by the new-meeting row, searched by the meeting's title for the
// brief. Every value here is invented.

const rules = vi.fn<(team: string | IntegrationScope) => Promise<MaterialUploadRules>>();
const list = vi.fn<(teamId: string) => Promise<Material[]>>();
const search =
  vi.fn<(team: string | IntegrationScope, question: string) => Promise<MaterialSearchAnswer>>();
vi.mock("./api", () => ({
  getMaterialUploadRules: (team: string | IntegrationScope) => rules(team),
  listMaterials: (teamId: string) => list(teamId),
  searchMaterials: (team: string | IntegrationScope, question: string) => search(team, question),
}));

afterEach(() => {
  rules.mockReset();
  list.mockReset();
  search.mockReset();
});

const RULES: MaterialUploadRules = {
  enabled: true,
  max_bytes: 10 * 1024 * 1024,
  suffixes: [".txt", ".md", ".csv"],
  max_title_chars: 300,
  max_materials: 50,
  search: true,
  max_question_chars: 300,
};
const OFF: MaterialUploadRules = { ...RULES, enabled: false, search: false };

const material = (overrides: Partial<Material>): Material => ({
  id: "mat_1",
  team_id: "team_1",
  title: "출시 계획",
  source: "upload",
  drive_file_id: null,
  drive_kind: null,
  created_at: "2026-10-10T01:00:00Z",
  expires_at: null,
  not_read: [],
  ...overrides,
});

const answer = (titles: string[]): MaterialSearchAnswer => ({
  hits: titles.map((title, index) => ({
    material_id: `mat_${index}`,
    title,
    excerpt: `${title}의 발췌 01*-****-****`,
  })),
  notice: null,
  more: false,
});

const ABOUT = { meetingId: "mtg_next", title: "  출시   점검 회의 " };

describe("agendaQuestion", () => {
  it("is the title with its spaces folded, and nothing else", () => {
    expect(agendaQuestion(ABOUT.title)).toBe("출시 점검 회의");
  });

  it("is cut to the longest question the search takes", () => {
    expect(agendaQuestion("가".repeat(400))).toHaveLength(300);
  });

  it("is empty for a blank title", () => {
    expect(agendaQuestion(" \n ")).toBe("");
  });
});

describe("MATERIALS_AGENDA, for the new-meeting row", () => {
  it("is named for what it reads", () => {
    expect(MATERIALS_AGENDA.label).toBe("올린 자료");
  });

  it("lists the team's uploads by title, and no Drive link", async () => {
    rules.mockResolvedValue(RULES);
    list.mockResolvedValue([
      material({ id: "mat_1", title: "출시 계획" }),
      material({
        id: "mat_2",
        title: "드라이브 문서",
        source: "drive_link",
        drive_file_id: "abc",
        drive_kind: "document",
      }),
    ]);

    await expect(MATERIALS_AGENDA.lines("team_1")).resolves.toEqual([{ title: "출시 계획" }]);
    expect(rules).toHaveBeenCalledWith("team_1");
    expect(list).toHaveBeenCalledWith("team_1");
  });

  it("has nothing, and lists nothing, where the server has uploads off", async () => {
    rules.mockResolvedValue(OFF);

    await expect(MATERIALS_AGENDA.lines("team_1")).resolves.toEqual([]);
    expect(list).not.toHaveBeenCalled();
  });

  it("has nothing where uploads are on but the server does not say it searches", async () => {
    rules.mockResolvedValue({ ...RULES, search: undefined });

    await expect(MATERIALS_AGENDA.lines("team_1")).resolves.toEqual([]);
    expect(list).not.toHaveBeenCalled();
  });

  it("has nothing for a team with only Drive links", async () => {
    rules.mockResolvedValue(RULES);
    list.mockResolvedValue([
      material({ source: "drive_link", drive_file_id: "abc", drive_kind: "file" }),
    ]);

    await expect(MATERIALS_AGENDA.lines("team_1")).resolves.toEqual([]);
  });

  it("fails as the rules fail, which the row counts as nothing", async () => {
    rules.mockRejectedValue(new ApiError(404, "unknown", "Not Found", {}));

    await expect(MATERIALS_AGENDA.lines("team_1")).rejects.toBeInstanceOf(ApiError);
    expect(list).not.toHaveBeenCalled();
  });
});

describe("MATERIALS_BRIEF_AGENDA, for the brief's draft", () => {
  it("is named for how its lines were found", () => {
    expect(MATERIALS_BRIEF_AGENDA.label).toBe("회의 제목과 가까운 올린 자료");
  });

  it("asks by the brief's meeting with its title, and lists each material by title only", async () => {
    rules.mockResolvedValue(RULES);
    search.mockResolvedValue(answer(["출시 계획", "QA 체크리스트"]));

    const lines = await MATERIALS_BRIEF_AGENDA.lines("mtg_earlier", ABOUT);

    expect(lines).toEqual([{ title: "출시 계획" }, { title: "QA 체크리스트" }]);
    // No excerpt: the draft's other carried groups show a title and nothing under it.
    expect(JSON.stringify(lines)).not.toContain("발췌");
    expect(rules).toHaveBeenCalledWith({ meetingId: "mtg_next" });
    expect(search).toHaveBeenCalledWith({ meetingId: "mtg_next" }, "출시 점검 회의");
  });

  it("has nothing, and asks no question, where the server has uploads off", async () => {
    rules.mockResolvedValue(OFF);

    await expect(MATERIALS_BRIEF_AGENDA.lines("mtg_earlier", ABOUT)).resolves.toEqual([]);
    expect(search).not.toHaveBeenCalled();
  });

  it("has nothing, and asks nothing, for a meeting with a blank title", async () => {
    await expect(
      MATERIALS_BRIEF_AGENDA.lines("mtg_earlier", { meetingId: "mtg_next", title: "  " }),
    ).resolves.toEqual([]);
    expect(rules).not.toHaveBeenCalled();
    expect(search).not.toHaveBeenCalled();
  });

  it("has nothing when the search finds nothing", async () => {
    rules.mockResolvedValue(RULES);
    search.mockResolvedValue(answer([]));

    await expect(MATERIALS_BRIEF_AGENDA.lines("mtg_earlier", ABOUT)).resolves.toEqual([]);
  });

  it("drops a material deleted since the last read: nothing of it is kept here", async () => {
    rules.mockResolvedValue(RULES);
    search
      .mockResolvedValueOnce(answer(["출시 계획", "지워질 자료"]))
      .mockResolvedValueOnce(answer(["출시 계획"]));

    await expect(MATERIALS_BRIEF_AGENDA.lines("mtg_earlier", ABOUT)).resolves.toEqual([
      { title: "출시 계획" },
      { title: "지워질 자료" },
    ]);
    await expect(MATERIALS_BRIEF_AGENDA.lines("mtg_earlier", ABOUT)).resolves.toEqual([
      { title: "출시 계획" },
    ]);
  });

  it("fails as the search fails, which the draft counts as answered with nothing", async () => {
    rules.mockResolvedValue(RULES);
    search.mockRejectedValue(new ApiError(404, "not_found", "meeting not found", {}));

    await expect(MATERIALS_BRIEF_AGENDA.lines("mtg_earlier", ABOUT)).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});
