import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { MAX_QUESTION_CHARS } from "../materialSearch";
import type { Material, MaterialSearchAnswer, MaterialUploadRules } from "../types";
import { MaterialsScreen } from "./MaterialsScreen";

// The 자료 screen's question box (#817). What is under test is that the box
// exists only where the server says it answers, that the question is kept
// nowhere, and that an excerpt is shown as the masked text it is and does
// not outlive its material on the screen. Every word here is made up.

const session = vi.fn();
const list = vi.fn();
const rules = vi.fn();
const search = vi.fn();
const remove = vi.fn();
vi.mock("@/shared/api/auth", () => ({ getSession: () => session() }));
vi.mock("../api", () => ({
  getMaterialUploadRules: (teamId: string) => rules(teamId),
  listMaterials: (teamId: string) => list(teamId),
  registerMaterial: vi.fn(),
  uploadMaterial: vi.fn(),
  searchMaterials: (teamId: string, question: string) => search(teamId, question),
  deleteMaterial: (teamId: string, id: string) => remove(teamId, id),
}));

const me = () => ({
  id: "user_me",
  email: "me@example.com",
  display_name: "Me",
  teams: [{ id: "team_a", name: "가 팀" }],
});

const RULES: MaterialUploadRules = {
  enabled: true,
  max_bytes: 5 * 1024 * 1024,
  suffixes: [".txt", ".pdf"],
  max_title_chars: 80,
  max_materials: 200,
  search: true,
  // Not the client's fallback, so a number on screen can only be the answer's.
  max_question_chars: 120,
};

const uploaded = (id: string, title: string): Material => ({
  id,
  team_id: "team_a",
  title,
  source: "upload",
  drive_file_id: null,
  drive_kind: null,
  created_at: "2026-10-09T03:00:00Z",
  expires_at: "2099-01-07T03:00:00Z",
  not_read: [],
});

const STARRED = "별표(*)로 보이는 부분은 개인정보로 보여 가려진 값일 수 있습니다.";
const answer = (over: Partial<MaterialSearchAnswer> = {}): MaterialSearchAnswer => ({
  hits: [
    { material_id: "mat_plan", title: "분기 계획", excerpt: "…출시는 11월 둘째 주로 잡는다. 담당 연락처 ***-****-****…" },
    { material_id: "mat_notes", title: "회고 메모", excerpt: "출시 뒤 한 주는 <b>기능 동결</b>" },
  ],
  notice: STARRED,
  more: false,
  ...over,
});

const box = () => screen.getByLabelText("자료에 물을 내용") as HTMLInputElement;
const ask = (value: string) => fireEvent.change(box(), { target: { value } });
const find = () => screen.getByRole("button", { name: "찾기" });
const found = () => screen.queryByRole("region", { name: "찾은 내용" });

const open = async (served: unknown = RULES, materials: Material[] = []) => {
  session.mockResolvedValue(me());
  list.mockResolvedValue(materials);
  rules.mockResolvedValue(served);
  render(<MaterialsScreen />);
  await screen.findByRole("form", { name: "자료 등록" });
  await waitFor(() => expect(rules).toHaveBeenCalled());
};

afterEach(() => {
  cleanup();
  for (const fake of [session, list, rules, search, remove]) fake.mockReset();
});

describe("MaterialsScreen, whether a question can be asked at all", () => {
  it.each([
    ["takes uploads and says nothing of a search", { ...RULES, search: undefined }],
    ["says it has no search", { ...RULES, search: false }],
    ["says something that is not yes", { ...RULES, search: "soon" }],
    ["has uploads off", { ...RULES, enabled: false }],
  ])("draws no box on a server that %s", async (_name, served) => {
    await open(served);
    // The upload form, where there is one, has been drawn by now.
    if ((served as MaterialUploadRules).enabled) {
      await screen.findByRole("form", { name: "파일 올리기" });
    }

    expect(screen.queryByRole("form", { name: "자료 검색" })).toBeNull();
    expect(screen.queryByLabelText("자료에 물을 내용")).toBeNull();
  });

  it("says, before anything is asked, what is searched -- and claims nothing it cannot show", async () => {
    await open();
    const section = await screen.findByRole("region", { name: "올린 파일에서 찾기" });

    expect(section.textContent).toContain("올린 파일에서 보관 중인 글만 찾습니다");
    expect(section.textContent).toContain("링크로 등록한 Drive 파일은");
    expect(section.textContent).toContain("개인정보를 가린 글의 일부");
    // What the embedding server keeps of a question is not known here.
    expect(section.textContent).not.toMatch(/저장하지 않|보관하지 않|남기지 않/);
  });
});

describe("MaterialsScreen, asking the uploaded materials", () => {
  it("sends the question for the team, and shows each material's title and excerpt as text", async () => {
    await open();
    await screen.findByRole("form", { name: "자료 검색" });
    search.mockResolvedValue(answer());

    ask("  출시 일정  ");
    fireEvent.click(find());

    const region = await screen.findByRole("region", { name: "찾은 내용" });
    expect(search).toHaveBeenCalledExactlyOnceWith("team_a", "출시 일정");
    const hits = within(region).getAllByRole("listitem");
    expect(hits).toHaveLength(2);
    expect(hits[0]?.textContent).toContain("분기 계획");
    expect(hits[0]?.textContent).toContain("…출시는 11월 둘째 주로 잡는다. 담당 연락처 ***-****-****…");
    // Text, not markup: what a file said is never run as the page's own.
    expect(hits[1]?.textContent).toContain("출시 뒤 한 주는 <b>기능 동결</b>");
    expect(hits[1]?.querySelector("b")).toBeNull();
    // A hit names its material and opens nothing: there is no original.
    expect(within(region).queryAllByRole("link")).toHaveLength(0);
  });

  it("shows the server's notice as it is, and says so when more materials answered", async () => {
    await open();
    await screen.findByRole("form", { name: "자료 검색" });
    search.mockResolvedValue(answer({ more: true }));

    ask("출시 일정");
    fireEvent.click(find());

    const region = await screen.findByRole("region", { name: "찾은 내용" });
    expect(within(region).getByText(STARRED)).toBeTruthy();
    expect(region.textContent).toContain("이 밖에도 답이 될 만한 자료가 더 있습니다");
  });

  it("says nothing of more, and shows no notice, when the answer has neither", async () => {
    await open();
    await screen.findByRole("form", { name: "자료 검색" });
    search.mockResolvedValue(answer({ notice: null }));

    ask("출시 일정");
    fireEvent.click(find());

    const region = await screen.findByRole("region", { name: "찾은 내용" });
    expect(region.textContent).not.toContain("이 밖에도");
    expect(region.textContent).not.toContain("별표");
  });

  it("says nothing was found, and still shows a notice that came with no hit", async () => {
    await open();
    await screen.findByRole("form", { name: "자료 검색" });
    const asked = "질문에 전화번호처럼 개인정보를 가리키는 말이 있습니다.";
    search.mockResolvedValue({ hits: [], notice: asked, more: false });

    ask("김 대리 전화번호");
    fireEvent.click(find());

    const region = await screen.findByRole("region", { name: "찾은 내용" });
    expect(region.textContent).toContain("올린 파일에서 찾은 내용이 없습니다");
    expect(within(region).getByText(asked)).toBeTruthy();
    expect(within(region).queryAllByRole("listitem")).toHaveLength(0);
  });

  it("cannot be asked empty, by the button or by Enter", async () => {
    await open();
    const form = await screen.findByRole("form", { name: "자료 검색" });

    expect(find().hasAttribute("disabled")).toBe(true);
    ask("   ");
    expect(find().hasAttribute("disabled")).toBe(true);
    fireEvent.submit(form);
    ask("출시");
    expect(find().hasAttribute("disabled")).toBe(false);

    expect(search).not.toHaveBeenCalled();
  });

  it("waits for the one answer: no second question over the first", async () => {
    await open();
    const form = await screen.findByRole("form", { name: "자료 검색" });
    let finish: (value: MaterialSearchAnswer) => void = () => undefined;
    search.mockReturnValue(new Promise<MaterialSearchAnswer>((resolve) => (finish = resolve)));
    ask("출시 일정");

    fireEvent.click(find());
    fireEvent.submit(form);
    finish(answer());

    await screen.findByRole("region", { name: "찾은 내용" });
    expect(search).toHaveBeenCalledTimes(1);
  });

  it("takes an answer away when the question under it changes", async () => {
    await open();
    await screen.findByRole("form", { name: "자료 검색" });
    search.mockResolvedValue(answer());
    ask("출시 일정");
    fireEvent.click(find());
    await screen.findByRole("region", { name: "찾은 내용" });

    ask("출시 일정과 담당");

    expect(found()).toBeNull();
  });

  it("takes the last answer away while the next question is out", async () => {
    await open();
    await screen.findByRole("form", { name: "자료 검색" });
    search.mockResolvedValueOnce(answer());
    ask("출시 일정");
    fireEvent.click(find());
    await screen.findByRole("region", { name: "찾은 내용" });

    search.mockReturnValueOnce(new Promise<MaterialSearchAnswer>(() => undefined));
    fireEvent.submit(screen.getByRole("form", { name: "자료 검색" }));

    await waitFor(() => expect(search).toHaveBeenCalledTimes(2));
    expect(found()).toBeNull();
  });
});

describe("MaterialsScreen, where the question is kept", () => {
  it("asks the browser not to remember it, stops at the server's length, and stores nothing", async () => {
    await open();
    await screen.findByRole("form", { name: "자료 검색" });
    search.mockResolvedValue(answer());
    const before = window.location.href;

    expect(box().getAttribute("autocomplete")).toBe("off");
    expect(box().maxLength).toBe(120);
    ask("김 대리 출시 일정");
    fireEvent.click(find());
    await screen.findByRole("region", { name: "찾은 내용" });

    expect(window.location.href).toBe(before);
    for (const store of [window.localStorage, window.sessionStorage]) {
      for (let i = 0; i < store.length; i += 1) {
        expect(store.getItem(store.key(i) ?? "") ?? "").not.toContain("김 대리");
      }
    }
  });
});

describe("MaterialsScreen, an excerpt and its material", () => {
  const remove1 = async (name: string) => {
    const row = within(screen.getByRole("list", { name: "등록한 자료" }))
      .getAllByRole("listitem")
      .find((item) => item.textContent?.includes(name)) as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "삭제" }));
    fireEvent.click(
      within(within(row).getByRole("group", { name: "자료 삭제 확인" })).getByRole("button", {
        name: "삭제",
      }),
    );
    await waitFor(() => expect(remove).toHaveBeenCalledWith("team_a", expect.any(String)));
  };

  it("drops the excerpt of a material deleted on the screen, and keeps the others", async () => {
    await open(RULES, [uploaded("mat_plan", "분기 계획"), uploaded("mat_notes", "회고 메모")]);
    await screen.findByRole("form", { name: "자료 검색" });
    search.mockResolvedValue(answer());
    remove.mockResolvedValue(undefined);
    ask("출시 일정");
    fireEvent.click(find());
    const region = await screen.findByRole("region", { name: "찾은 내용" });
    expect(within(region).getAllByRole("listitem")).toHaveLength(2);

    await remove1("분기 계획");

    await waitFor(() => expect(within(region).getAllByRole("listitem")).toHaveLength(1));
    expect(region.textContent).not.toContain("11월 둘째 주");
    expect(region.textContent).toContain("기능 동결");
  });

  it("drops it as well when the delete finds the material already gone", async () => {
    await open(RULES, [uploaded("mat_plan", "분기 계획"), uploaded("mat_notes", "회고 메모")]);
    await screen.findByRole("form", { name: "자료 검색" });
    search.mockResolvedValue(answer());
    remove.mockRejectedValue(new ApiError(404, "not_found", "no such material"));
    ask("출시 일정");
    fireEvent.click(find());
    const region = await screen.findByRole("region", { name: "찾은 내용" });

    await remove1("분기 계획");

    await waitFor(() => expect(within(region).getAllByRole("listitem")).toHaveLength(1));
    expect(region.textContent).not.toContain("11월 둘째 주");
  });

  it("keeps it when the delete failed, since the text is still there", async () => {
    await open(RULES, [uploaded("mat_plan", "분기 계획")]);
    await screen.findByRole("form", { name: "자료 검색" });
    search.mockResolvedValue(answer());
    remove.mockRejectedValue(new ApiError(500, "unknown", "boom"));
    ask("출시 일정");
    fireEvent.click(find());
    const region = await screen.findByRole("region", { name: "찾은 내용" });

    await remove1("분기 계획");

    await screen.findByText(/삭제하지 못했습니다/);
    expect(within(region).getAllByRole("listitem")).toHaveLength(2);
  });

  it("leaves nothing of an answer -- not its notice either -- once every material in it is gone", async () => {
    await open(RULES, [uploaded("mat_plan", "분기 계획")]);
    await screen.findByRole("form", { name: "자료 검색" });
    search.mockResolvedValue(
      answer({ hits: [{ material_id: "mat_plan", title: "분기 계획", excerpt: "연락처 ***" }] }),
    );
    remove.mockResolvedValue(undefined);
    ask("연락처");
    fireEvent.click(find());
    await screen.findByRole("region", { name: "찾은 내용" });

    await remove1("분기 계획");

    await waitFor(() => expect(found()).toBeNull());
    expect(screen.queryByText(STARRED)).toBeNull();
    expect(screen.queryByText(/찾은 내용이 없습니다/)).toBeNull();
  });
});

describe("MaterialsScreen, a question that got no answer", () => {
  const refused = async (cause: unknown) => {
    await open();
    await screen.findByRole("form", { name: "자료 검색" });
    search.mockRejectedValue(cause);
    ask("출시 일정");
    fireEvent.click(find());
    const section = screen.getByRole("region", { name: "올린 파일에서 찾기" });
    return (await within(section).findByRole("alert")).textContent;
  };

  it("says the length when the server refuses the question", async () => {
    expect(await refused(new ApiError(422, "validation_error", "too long"))).toBe(
      "질문은 120자까지 적을 수 있습니다. 줄여서 다시 찾아 주세요.",
    );
  });

  it.each([
    ["sends no number", undefined],
    ["sends none that is a length", 0],
  ])("stops at the length it knows of where the server %s", async (_name, said) => {
    await open({ ...RULES, max_question_chars: said });
    await screen.findByRole("form", { name: "자료 검색" });

    expect(box().maxLength).toBe(MAX_QUESTION_CHARS);
  });

  it("tells a team that cannot be read from a server without the search", async () => {
    expect(await refused(new ApiError(404, "not_found", "no such team"))).toBe(
      "이 팀의 자료를 볼 수 없습니다.",
    );
    cleanup();
    expect(await refused(new ApiError(404, "unknown", "Not Found"))).toBe(
      "이 서버에서는 올린 파일에서 찾기를 쓰지 않습니다.",
    );
  });

  it("asks to try again after anything else, and shows no answer", async () => {
    expect(await refused(new TypeError("Failed to fetch"))).toBe(
      "찾지 못했습니다. 잠시 후 다시 시도해 주세요.",
    );
    expect(found()).toBeNull();
    cleanup();
    expect(await refused(new ApiError(500, "unknown", "boom"))).toBe(
      "찾지 못했습니다. 잠시 후 다시 시도해 주세요.",
    );
  });

  it("takes the refusal away when the question changes", async () => {
    await refused(new ApiError(500, "unknown", "boom"));

    ask("출시");

    const section = screen.getByRole("region", { name: "올린 파일에서 찾기" });
    expect(within(section).queryByRole("alert")).toBeNull();
  });
});
