import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api/client";

import {
  CONFIDENTIAL_REFUSAL,
  UNANSWERED,
  deletionDay,
  fileRefusal,
  mayHaveStored,
  megabytes,
  notReadNote,
  shelfFull,
  suffixOf,
  uploadRefusal,
} from "./materialUpload";
import type { MaterialUploadRules } from "./types";

// What the 자료 screen says about a file a member sends (#817). Made-up names
// and sizes only: nothing here is a file.

const RULES: MaterialUploadRules = {
  enabled: true,
  max_bytes: 10 * 1024 * 1024,
  suffixes: [".txt", ".md", ".csv", ".docx", ".xlsx", ".pptx", ".pdf", ".xls", ".ppt"],
  max_title_chars: 120,
  max_materials: 200,
};

const refused = (
  message: string,
  details: Record<string, unknown> = { field: "file" },
  code = "validation_error",
  status = 422,
) => new ApiError(status, code, message, details);

describe("a file's ending and size, before it is sent", () => {
  it("reads the ending as the server does: the last one, in any case", () => {
    expect(suffixOf("plan.PDF")).toBe(".pdf");
    expect(suffixOf("notes.v2.docx")).toBe(".docx");
    expect(suffixOf("README")).toBe("");
    expect(suffixOf(".pdf")).toBe("");
  });

  it("lets a file the rules take go to the server", () => {
    expect(fileRefusal({ name: "plan.PDF", size: 1 }, RULES)).toBeNull();
    expect(fileRefusal({ name: "plan.pdf", size: RULES.max_bytes }, RULES)).toBeNull();
  });

  it("stops an ending the server does not take, and names the ones it does", () => {
    const said = fileRefusal({ name: "plan.hwp", size: 10 }, RULES);

    expect(said).toMatch(/이 형식의 파일은 받지 않습니다/);
    expect(said).toContain(".docx");
    expect(fileRefusal({ name: "pdf", size: 10 }, RULES)).toMatch(/받지 않습니다/);
  });

  it("stops a file one byte over the limit, in the server's number", () => {
    const said = fileRefusal({ name: "plan.pdf", size: RULES.max_bytes + 1 }, RULES);

    expect(said).toBe("파일이 너무 큽니다. 한 파일은 10MB까지 올릴 수 있습니다.");
    expect(megabytes(1024 * 1024 * 1.5)).toBe("1.5MB");
  });

  it("stops an empty file", () => {
    expect(fileRefusal({ name: "plan.txt", size: 0 }, RULES)).toBe("읽을 글이 없는 파일입니다.");
  });
});

describe("a refusal, in the member's words", () => {
  it.each([
    ["protected", /암호가 걸린 파일/],
    ["no_text_layer", /글자 정보가 없는 PDF/],
    ["page_without_text", /그림으로만 된 쪽이 있는 PDF/],
    ["damaged", /손상되어/],
    ["encoding", /UTF-8로 저장해/],
    ["empty", /읽을 글이 없는/],
    ["too_long", /30만 자/],
    ["unsupported_type", /이 형식의 파일은 받지 않습니다/],
    ["too_large", /10MB까지/],
  ])("says why a file could not be read: %s", (reason, sentence) => {
    expect(uploadRefusal(refused(`the file cannot be read: ${reason}`), RULES)).toMatch(sentence);
  });

  it("reads the reason from the details when the server puts it there", () => {
    const cause = refused("the file cannot be read", { field: "file", reason: "protected" });

    expect(uploadRefusal(cause, RULES)).toMatch(/암호가 걸린 파일/);
  });

  it("falls back to a plain sentence for a reason it does not know", () => {
    expect(uploadRefusal(refused("the file cannot be read: something_new"), RULES)).toBe(
      "파일을 받지 못했습니다. 제목과 파일을 확인해 주세요.",
    );
  });

  it("says what a marked file's refusal means, and what the approvers are not told", () => {
    const cause = refused(
      "the file is marked confidential and was not taken",
      {},
      "confidential_file",
    );

    const said = uploadRefusal(cause, RULES);

    expect(said).toBe(CONFIDENTIAL_REFUSAL);
    expect(said).toMatch(/저장되지 않았습니다/);
    expect(said).toMatch(/올린 사람과 파일 이름은 알려지지 않습니다/);
  });

  it("says the team's shelf is full, in the server's number, by the reason it gives", () => {
    const rules = { ...RULES, max_materials: 150 };
    const cause = refused("the shelf is full", { field: "file", reason: "shelf_full" });

    expect(uploadRefusal(cause, rules)).toBe(
      "한 팀이 둘 수 있는 자료는 150개까지입니다. 쓰지 않는 자료를 삭제한 뒤 다시 시도해 주세요.",
    );
    // The same refusal of a link, whose field is not the file.
    expect(
      shelfFull(refused("the shelf is full", { field: "link", reason: "shelf_full" }), rules),
    ).toMatch(/150개까지/);
    expect(shelfFull(cause, null)).toMatch(/팀이 둘 수 있는 자료 수를 넘었습니다/);
  });

  it("reads a full shelf from the message of a server that gives no reason", () => {
    expect(uploadRefusal(refused("a team keeps at most 200 materials"), RULES)).toMatch(
      /200개까지/,
    );
  });

  it("does not read any other refusal as a full shelf", () => {
    expect(shelfFull(refused("the file cannot be read: damaged"), RULES)).toBeNull();
    expect(shelfFull(refused("a title has at most 120 characters", { field: "title" }), RULES)).toBeNull();
    expect(shelfFull(new ApiError(500, "unknown", "boom", { reason: "shelf_full" }), RULES)).toBeNull();
    expect(shelfFull(new Error("shelf_full"), RULES)).toBeNull();
  });

  it("says which kind of value to take out of a title refused as personal data", () => {
    const cause = refused("the title reads as personal data", {
      field: "title",
      reason: "personal_data",
      categories: ["phone"],
    });

    expect(uploadRefusal(cause, RULES)).toMatch(/전화번호로 보이는 값/);
  });

  it("says a title is missing or too long, with the limit", () => {
    const cause = refused("a title has at most 120 characters", { field: "title" });

    expect(uploadRefusal(cause, RULES)).toMatch(/120자까지/);
  });

  it("tells a deployment without uploads from a team the reader is not in", () => {
    const off = new ApiError(404, "unknown", "Not Found");
    const stranger = new ApiError(404, "not_found", "no such team");

    expect(uploadRefusal(off, RULES)).toBe("이 서버에서는 파일 올리기를 쓰지 않습니다.");
    expect(uploadRefusal(stranger, RULES)).toBe("이 팀의 자료를 볼 수 없습니다.");
  });

  it("reads a proxy's 413 as the size limit", () => {
    expect(uploadRefusal(new ApiError(413, "unknown", "Payload Too Large"), RULES)).toMatch(
      /10MB까지/,
    );
  });
});

describe("an upload with no answer", () => {
  it("does not claim nothing was stored: a dropped connection or a 5xx may have stored the row", () => {
    const dropped = new TypeError("Failed to fetch");
    const failed = new ApiError(502, "unknown", "Bad Gateway");

    expect(mayHaveStored(dropped)).toBe(true);
    expect(mayHaveStored(failed)).toBe(true);
    expect(mayHaveStored(new ApiError(500, "internal_error", "boom"))).toBe(true);
    expect(uploadRefusal(dropped, RULES)).toBe(UNANSWERED);
    expect(uploadRefusal(failed, RULES)).toBe(UNANSWERED);
    expect(UNANSWERED).toMatch(/목록에 자료가 보이지 않으면/);
  });

  it("knows a refusal stored nothing", () => {
    expect(mayHaveStored(refused("the file cannot be read: damaged"))).toBe(false);
    expect(mayHaveStored(new ApiError(404, "unknown", "Not Found"))).toBe(false);
  });
});

describe("what a stored file held that was not read", () => {
  it("names each kind once, in the server's order", () => {
    expect(notReadNote(["pictures", "charts", "pictures"])).toBe(
      "이 파일의 그림, 차트 안에 있는 글은 읽지 않았습니다. 그 글은 보관되지 않고 검색에도 나오지 않습니다.",
    );
    expect(notReadNote(["embedded_files"])).toMatch(/첨부된 파일 안에 있는 글/);
  });

  it("says nothing when everything was read, or for a kind it does not know", () => {
    expect(notReadNote([])).toBeNull();
    expect(notReadNote(["something_new"])).toBeNull();
  });
});

describe("the day a row's text is deleted", () => {
  const now = new Date(2026, 9, 9, 12, 0, 0);

  it("is the day where the viewer is", () => {
    const at = new Date(2027, 0, 7, 0, 30, 0);

    expect(deletionDay(at.toISOString(), now)).toBe("2027-01-07 삭제 예정");
  });

  it("reads as soon once the time has come, since the task runs hourly", () => {
    expect(deletionDay(new Date(2026, 9, 9, 11, 59, 0).toISOString(), now)).toBe("곧 삭제");
    expect(deletionDay(now.toISOString(), now)).toBe("곧 삭제");
  });

  it("is nothing for a link, which keeps no text, and for a time it cannot read", () => {
    expect(deletionDay(null, now)).toBeNull();
    expect(deletionDay("not a time", now)).toBeNull();
  });
});
