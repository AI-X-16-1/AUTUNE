import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { localToday } from "../dates";
import { CONFIDENTIAL_REFUSAL, UNANSWERED } from "../materialUpload";
import type { Material, MaterialUploadRules } from "../types";
import { MaterialsScreen } from "./MaterialsScreen";

// The 자료 screen where the deployment takes uploads (#817). What is under
// test is what the screen promises about a file -- no original, masked text,
// a day it goes -- and that it sends nothing the rules already refuse. The
// files are a few made-up words; no real document is near this.

const session = vi.fn();
const list = vi.fn();
const rules = vi.fn();
const register = vi.fn();
const upload = vi.fn();
const remove = vi.fn();
vi.mock("@/shared/api/auth", () => ({ getSession: () => session() }));
vi.mock("../api", () => ({
  getMaterialUploadRules: (teamId: string) => rules(teamId),
  listMaterials: (teamId: string) => list(teamId),
  registerMaterial: (teamId: string, draft: unknown) => register(teamId, draft),
  uploadMaterial: (teamId: string, draft: unknown) => upload(teamId, draft),
  deleteMaterial: (teamId: string, id: string) => remove(teamId, id),
}));

const me = () => ({
  id: "user_me",
  email: "me@example.com",
  display_name: "Me",
  teams: [{ id: "team_a", name: "가 팀" }],
});

// Not the server's defaults, so a number on screen can only be the answer's.
const RULES: MaterialUploadRules = {
  enabled: true,
  max_bytes: 5 * 1024 * 1024,
  suffixes: [".txt", ".pdf", ".docx"],
  max_title_chars: 80,
  max_materials: 200,
};

const EXPIRES = "2099-01-07T03:00:00Z";
const uploaded = (over: Partial<Material> = {}): Material => ({
  id: "mat_up",
  team_id: "team_a",
  title: "분기 계획",
  source: "upload",
  drive_file_id: null,
  drive_kind: null,
  created_at: "2026-10-09T03:00:00Z",
  expires_at: EXPIRES,
  not_read: [],
  ...over,
});
const linked = (): Material => ({
  id: "mat_link",
  team_id: "team_a",
  title: "3분기 로드맵",
  source: "drive_link",
  drive_file_id: "1ZyXwVuTsRqPoNmLkJiHgFeDcBa987654",
  drive_kind: "document",
  created_at: "2026-10-07T03:00:00Z",
  expires_at: null,
  not_read: [],
});

const file = (name = "분기 계획.pdf", size = 13) =>
  new File(["x".repeat(size)], name, { type: "application/octet-stream" });
const choose = (chosen: File) =>
  fireEvent.change(screen.getByLabelText("올릴 파일"), { target: { files: [chosen] } });
const title = (value: string) =>
  fireEvent.change(screen.getByLabelText("올릴 자료 제목"), { target: { value } });
const send = () => screen.getByRole("button", { name: "파일 올리기" });
const rows = () =>
  within(screen.getByRole("list", { name: "등록한 자료" })).getAllByRole("listitem");

const open = async (materials: Material[] = [], answer: unknown = RULES) => {
  session.mockResolvedValue(me());
  list.mockResolvedValue(materials);
  if (answer instanceof Error) rules.mockRejectedValue(answer);
  else rules.mockResolvedValue(answer);
  render(<MaterialsScreen />);
  await screen.findByRole("form", { name: "자료 등록" });
};

afterEach(() => {
  cleanup();
  for (const fake of [session, list, rules, register, upload, remove]) fake.mockReset();
});

describe("MaterialsScreen, whether a file can be sent at all", () => {
  it("shows no upload where the deployment has it off", async () => {
    await open([], { ...RULES, enabled: false });

    await waitFor(() => expect(rules).toHaveBeenCalledExactlyOnceWith("team_a"));
    expect(screen.queryByRole("form", { name: "파일 올리기" })).toBeNull();
    expect(screen.queryByText(/파일을 올려 둘 수도 있습니다/)).toBeNull();
  });

  it("shows no upload when the server has no such route, and the link shelf still works", async () => {
    await open([linked()], new ApiError(404, "unknown", "Not Found"));

    expect(await screen.findByText("3분기 로드맵")).toBeTruthy();
    expect(screen.queryByRole("form", { name: "파일 올리기" })).toBeNull();
  });

  it("says, before anything is sent, what is kept of a file and in the server's own limits", async () => {
    await open();

    const form = await screen.findByRole("form", { name: "파일 올리기" });
    expect(within(form).getByText(/원본은 보관하지 않으며/)).toBeTruthy();
    expect(within(form).getByText(/개인정보를 가린 글/)).toBeTruthy();
    expect(within(form).getByText(/문장 속의 이름은 가려지지 않으며/)).toBeTruthy();
    expect(within(form).getByText(/그림으로만 들어 있는 글은\s+읽지 않습니다/)).toBeTruthy();
    expect(within(form).getByText(/한 파일 5MB까지 · \.txt \.pdf \.docx/)).toBeTruthy();
    expect(screen.getByLabelText("올릴 파일").getAttribute("accept")).toBe(".txt,.pdf,.docx");
    expect(screen.getByLabelText("올릴 자료 제목").getAttribute("maxlength")).toBe("80");
  });
});

describe("MaterialsScreen, sending a file", () => {
  it("sends the title and the file, puts the row first and says until when its text is kept", async () => {
    await open([linked()]);
    await screen.findByRole("form", { name: "파일 올리기" });
    upload.mockResolvedValue(uploaded({ not_read: ["pictures"] }));
    const chosen = file();

    title("  분기 계획  ");
    choose(chosen);
    fireEvent.click(send());

    await waitFor(() => expect(rows()).toHaveLength(2));
    expect(upload).toHaveBeenCalledExactlyOnceWith("team_a", { title: "분기 계획", file: chosen });
    expect(within(rows()[0] as HTMLElement).getByText("분기 계획")).toBeTruthy();
    const said = screen.getByRole("status").textContent ?? "";
    expect(said).toContain(`개인정보를 가린 글을 ${localToday(new Date(EXPIRES))}까지 보관합니다`);
    expect(said).toContain("이 파일의 그림 안에 있는 글은 읽지 않았습니다");
    expect((screen.getByLabelText("올릴 자료 제목") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("올릴 파일") as HTMLInputElement).files).toHaveLength(0);
  });

  it("cannot be sent without a title or without a file", async () => {
    await open();
    await screen.findByRole("form", { name: "파일 올리기" });

    expect(send().hasAttribute("disabled")).toBe(true);
    title("분기 계획");
    expect(send().hasAttribute("disabled")).toBe(true);
    choose(file());
    expect(send().hasAttribute("disabled")).toBe(false);
    title("   ");
    expect(send().hasAttribute("disabled")).toBe(true);
  });

  it("never sends a file the rules already refuse, and says why when it is chosen", async () => {
    await open();
    await screen.findByRole("form", { name: "파일 올리기" });
    title("분기 계획");

    choose(file("분기 계획.hwp"));

    expect(screen.getByRole("alert").textContent).toMatch(/이 형식의 파일은 받지 않습니다/);
    expect(send().hasAttribute("disabled")).toBe(true);
    fireEvent.submit(screen.getByRole("form", { name: "파일 올리기" }));
    choose(file("큰 파일.pdf", RULES.max_bytes + 1));
    expect(screen.getByRole("alert").textContent).toMatch(/5MB까지/);
    fireEvent.submit(screen.getByRole("form", { name: "파일 올리기" }));
    expect(upload).not.toHaveBeenCalled();
  });

  it("waits for the one request: busy, and no second file over the first", async () => {
    await open();
    await screen.findByRole("form", { name: "파일 올리기" });
    let finish: (row: Material) => void = () => undefined;
    upload.mockReturnValue(new Promise<Material>((resolve) => (finish = resolve)));
    title("분기 계획");
    choose(file());

    fireEvent.click(send());

    expect(await screen.findByText(/파일을 읽고 개인정보를 가리는 중입니다/)).toBeTruthy();
    expect(screen.getByLabelText("올릴 파일").hasAttribute("disabled")).toBe(true);
    fireEvent.submit(screen.getByRole("form", { name: "파일 올리기" }));
    finish(uploaded());
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.queryByText(/파일을 읽고 개인정보를 가리는 중입니다/)).toBeNull();
    expect(upload).toHaveBeenCalledTimes(1);
  });

  it("says what a marked file's refusal means, and keeps what was typed", async () => {
    await open();
    await screen.findByRole("form", { name: "파일 올리기" });
    upload.mockRejectedValue(
      new ApiError(422, "confidential_file", "the file is marked confidential and was not taken"),
    );
    title("분기 계획");
    choose(file());

    fireEvent.click(send());

    expect((await screen.findByRole("alert")).textContent).toBe(CONFIDENTIAL_REFUSAL);
    expect((screen.getByLabelText("올릴 자료 제목") as HTMLInputElement).value).toBe("분기 계획");
    // A refusal stored nothing, so there is nothing to look for in the list.
    expect(list).toHaveBeenCalledTimes(1);
  });

  it("does not leave the last file's note standing under a refusal of the next", async () => {
    await open();
    await screen.findByRole("form", { name: "파일 올리기" });
    upload.mockResolvedValueOnce(uploaded());
    title("분기 계획");
    choose(file());
    fireEvent.click(send());
    expect(await screen.findByText(/자료를 올렸습니다/)).toBeTruthy();

    upload.mockRejectedValueOnce(
      new ApiError(422, "confidential_file", "the file is marked confidential and was not taken"),
    );
    title("대외비 문서");
    choose(file("대외비.pdf"));
    fireEvent.click(send());

    expect((await screen.findByRole("alert")).textContent).toBe(CONFIDENTIAL_REFUSAL);
    expect(screen.queryByText(/자료를 올렸습니다/)).toBeNull();
    expect(rows()).toHaveLength(1);
  });

  it("does not leave it standing under a refused link either", async () => {
    await open();
    await screen.findByRole("form", { name: "파일 올리기" });
    upload.mockResolvedValueOnce(uploaded());
    title("분기 계획");
    choose(file());
    fireEvent.click(send());
    expect(await screen.findByText(/자료를 올렸습니다/)).toBeTruthy();

    register.mockRejectedValue(new ApiError(422, "validation_error", "not a Drive link"));
    fireEvent.change(screen.getByLabelText("자료 제목"), { target: { value: "로드맵" } });
    fireEvent.change(screen.getByLabelText("Drive 링크"), {
      target: { value: "https://docs.google.com/document/d/1ZyXwVuTsRqPoNmLkJiHgFeDcBa987654/edit" },
    });
    fireEvent.click(screen.getByRole("button", { name: "자료 등록" }));

    expect((await screen.findByRole("alert")).textContent).toMatch(/등록하지 못했습니다/);
    expect(screen.queryByText(/자료를 올렸습니다/)).toBeNull();
  });

  it("reads the list again when the answer was lost, since the row may be there", async () => {
    await open();
    await screen.findByRole("form", { name: "파일 올리기" });
    upload.mockRejectedValue(new TypeError("Failed to fetch"));
    list.mockResolvedValue([uploaded()]);
    title("분기 계획");
    choose(file());

    fireEvent.click(send());

    expect((await screen.findByRole("alert")).textContent).toBe(UNANSWERED);
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(list).toHaveBeenCalledTimes(2);
  });
});

describe("MaterialsScreen, a full shelf", () => {
  it("says so to a link as to a file, with the server's limit, instead of blaming the link", async () => {
    await open([], { ...RULES, max_materials: 150 });
    await screen.findByRole("form", { name: "파일 올리기" });
    register.mockRejectedValue(
      new ApiError(422, "validation_error", "the shelf is full", {
        field: "link",
        reason: "shelf_full",
      }),
    );
    fireEvent.change(screen.getByLabelText("자료 제목"), { target: { value: "로드맵" } });
    fireEvent.change(screen.getByLabelText("Drive 링크"), {
      target: { value: "https://docs.google.com/document/d/1ZyXwVuTsRqPoNmLkJiHgFeDcBa987654/edit" },
    });

    fireEvent.click(screen.getByRole("button", { name: "자료 등록" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "한 팀이 둘 수 있는 자료는 150개까지입니다. 쓰지 않는 자료를 삭제한 뒤 다시 시도해 주세요.",
    );
  });
});

describe("MaterialsScreen, an uploaded row", () => {
  it("shows the day its text is deleted, and nothing to preview or open", async () => {
    await open([uploaded(), linked()]);

    const [up, link] = rows() as [HTMLElement, HTMLElement];
    expect(within(up).getByText(/올린 파일/).textContent).toContain(
      `${localToday(new Date(EXPIRES))} 삭제 예정`,
    );
    expect(within(up).queryByRole("button", { name: "미리보기" })).toBeNull();
    expect(within(up).queryByRole("link")).toBeNull();
    // A link keeps nothing, so it has no such day -- and keeps its two doors.
    expect(within(link).getByText(/Google 문서/).textContent).not.toContain("삭제");
    expect(within(link).getByRole("button", { name: "미리보기" })).toBeTruthy();
    expect(within(link).getByRole("link", { name: "Drive에서 열기" })).toBeTruthy();
  });

  it("asks before deleting, in words that say it cannot be undone", async () => {
    await open([uploaded(), linked()]);
    remove.mockResolvedValue(undefined);
    const up = rows()[0] as HTMLElement;

    fireEvent.click(within(up).getByRole("button", { name: "삭제" }));

    const ask = within(up).getByRole("group", { name: "자료 삭제 확인" });
    expect(ask.textContent).toMatch(/보관 중인 글이 바로 지워지고 되돌릴 수 없습니다/);
    expect(ask.textContent).toMatch(/원본 파일이 없어/);
    expect(ask.textContent).not.toMatch(/Drive/);
    expect(remove).not.toHaveBeenCalled();

    fireEvent.click(within(ask).getByRole("button", { name: "삭제" }));

    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(remove).toHaveBeenCalledExactlyOnceWith("team_a", "mat_up");
    expect(screen.getByRole("status").textContent).toBe(
      "자료를 삭제했습니다. 보관하던 글도 함께 지웠습니다.",
    );
  });

  it("drops a row that was already gone, and says so", async () => {
    await open([uploaded(), linked()]);
    remove.mockRejectedValue(new ApiError(404, "not_found", "no such material"));
    const up = rows()[0] as HTMLElement;

    fireEvent.click(within(up).getByRole("button", { name: "삭제" }));
    fireEvent.click(
      within(within(up).getByRole("group", { name: "자료 삭제 확인" })).getByRole("button", {
        name: "삭제",
      }),
    );

    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.getByRole("status").textContent).toBe("이미 삭제된 자료입니다.");
  });

  it("keeps the row when the delete fails, and does not say the Drive file stays", async () => {
    await open([uploaded()]);
    remove.mockRejectedValue(new ApiError(500, "unknown", "boom"));
    const up = rows()[0] as HTMLElement;

    fireEvent.click(within(up).getByRole("button", { name: "삭제" }));
    fireEvent.click(
      within(within(up).getByRole("group", { name: "자료 삭제 확인" })).getByRole("button", {
        name: "삭제",
      }),
    );

    expect(await screen.findByText("삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.")).toBeTruthy();
    expect(rows()).toHaveLength(1);
  });
});
