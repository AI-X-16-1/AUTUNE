import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { titleFromFileName } from "../materialUpload";
import type { MaterialUploadRules } from "../types";
import { MaterialUploadForm } from "./MaterialUploadForm";

// The file buttons and the title on the upload form: the file chooser is a
// button beside "Drive에서 선택", and a chosen file's name becomes the title
// unless the person already wrote one.

afterEach(cleanup);

const RULES: MaterialUploadRules = {
  enabled: true,
  max_bytes: 1024,
  suffixes: [".txt", ".pdf"],
  max_title_chars: 10,
  max_materials: 200,
};

const file = (name: string) => new File(["abc"], name);

const renderForm = (
  options: { pickDrive?: () => Promise<File | null>; showNotes?: boolean } = {},
) => {
  const onUpload = vi.fn(async () => undefined);
  render(
    <MaterialUploadForm
      rules={RULES}
      onUpload={onUpload}
      onUnanswered={vi.fn()}
      pickDrive={options.pickDrive}
      showNotes={options.showNotes}
    />,
  );
  return { onUpload };
};

const titleBox = () => screen.getByLabelText("올릴 자료 제목") as HTMLInputElement;
const choose = (chosen: File) =>
  fireEvent.change(screen.getByLabelText("올릴 파일"), { target: { files: [chosen] } });

describe("파일 선택", () => {
  it("is a button right before the Drive button, and it opens the file chooser", () => {
    const click = vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => undefined);
    renderForm({ pickDrive: async () => null });
    const chooser = screen.getByRole("button", { name: "파일 선택" });
    const drive = screen.getByRole("button", { name: "Drive에서 선택" });
    expect(chooser.nextElementSibling).toBe(drive);
    fireEvent.click(chooser);
    expect(click).toHaveBeenCalledTimes(1);
    click.mockRestore();
  });

  it("is drawn without the Drive button where no Picker is configured", () => {
    renderForm();
    expect(screen.getByRole("button", { name: "파일 선택" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Drive에서 선택" })).toBeNull();
  });

  it("names the chosen file", () => {
    renderForm();
    choose(file("계획.pdf"));
    expect(screen.getByText(/고른 파일: 계획\.pdf/)).toBeTruthy();
  });
});

describe("the title from the file's name", () => {
  it("fills an empty title with the name, without the ending, and leaves it editable", () => {
    renderForm();
    choose(file("계획.pdf"));
    expect(titleBox().value).toBe("계획");
    fireEvent.change(titleBox(), { target: { value: "계획 최종" } });
    expect(titleBox().value).toBe("계획 최종");
  });

  it("never replaces a title the person wrote", () => {
    renderForm();
    fireEvent.change(titleBox(), { target: { value: "내 제목" } });
    choose(file("계획.pdf"));
    expect(titleBox().value).toBe("내 제목");
  });

  it("replaces its own suggestion with the next file's name, but not an edited one", () => {
    renderForm();
    choose(file("첫째.pdf"));
    choose(file("둘째.pdf"));
    expect(titleBox().value).toBe("둘째");
    fireEvent.change(titleBox(), { target: { value: "둘째 수정" } });
    choose(file("셋째.pdf"));
    expect(titleBox().value).toBe("둘째 수정");
  });

  it("is cut to the title limit", () => {
    renderForm();
    choose(file("아주아주아주긴파일이름입니다.pdf"));
    expect(Array.from(titleBox().value)).toHaveLength(RULES.max_title_chars);
  });

  it("is filled from a file chosen in Drive too, and the upload carries it", async () => {
    const picked = file("회의록.txt");
    const { onUpload } = renderForm({ pickDrive: async () => picked });
    fireEvent.click(screen.getByRole("button", { name: "Drive에서 선택" }));
    await waitFor(() => expect(titleBox().value).toBe("회의록"));
    fireEvent.click(screen.getByRole("button", { name: "파일 올리기" }));
    await waitFor(() => expect(onUpload).toHaveBeenCalledWith({ title: "회의록", file: picked }));
  });

  it("starts from nothing again after a file was sent", async () => {
    const { onUpload } = renderForm();
    choose(file("첫째.pdf"));
    fireEvent.click(screen.getByRole("button", { name: "파일 올리기" }));
    await waitFor(() => expect(onUpload).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(titleBox().value).toBe(""));
    choose(file("둘째.pdf"));
    expect(titleBox().value).toBe("둘째");
  });
});

describe("titleFromFileName", () => {
  it("drops one ending, keeps the rest, and keeps a name that is only an ending", () => {
    expect(titleFromFileName("보고서.최종.docx", 50)).toBe("보고서.최종");
    expect(titleFromFileName("메모", 50)).toBe("메모");
    expect(titleFromFileName(".pdf", 50)).toBe(".pdf");
  });
});

describe("the notes under the form", () => {
  const notes = () => [
    screen.getByText(/원본은 보관하지 않으며/),
    screen.getByText(/Autune 서버는\s*Drive에 접속하지 않으며/),
  ];

  it("are shown by default", () => {
    renderForm({ pickDrive: async () => null });
    expect(notes().every((node) => !node.hidden)).toBe(true);
  });

  it("are hidden when the page folds them", () => {
    renderForm({ pickDrive: async () => null, showNotes: false });
    expect(notes().every((node) => node.hidden)).toBe(true);
  });
});
