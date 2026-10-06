import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DrivePreview } from "./DrivePreview";

// Google's own preview of a Drive file in a frame. Autune reads nothing; the
// frame can only be Google's preview of that file; and the way out -- why it
// may be empty, and a link to Drive -- is always on screen.

afterEach(cleanup);

const ID = "1AbC_dEf-GhIjKlMnOpQrStUvWxYz012345";

describe("DrivePreview", () => {
  it("frames Google's preview of the file, built from its id", () => {
    render(
      <DrivePreview
        link={`https://drive.google.com/file/d/${ID}/view?usp=sharing`}
      />,
    );

    const frame = screen.getByTitle(
      "Google Drive 자료 미리보기",
    ) as HTMLIFrameElement;
    expect(frame.getAttribute("src")).toBe(
      `https://drive.google.com/file/d/${ID}/preview`,
    );
  });

  it("frames a Google presentation through its own preview, under the title it is given", () => {
    render(
      <DrivePreview
        link={`https://docs.google.com/presentation/d/${ID}/edit`}
        title="3분기 계획"
      />,
    );

    expect(screen.getByTitle("3분기 계획").getAttribute("src")).toBe(
      `https://docs.google.com/presentation/d/${ID}/preview`,
    );
  });

  it("keeps the frame to what the preview needs and tells Google nothing of where it is shown", () => {
    render(<DrivePreview link={ID} />);

    const frame = screen.getByTitle("Google Drive 자료 미리보기");
    const sandbox = (frame.getAttribute("sandbox") ?? "").split(" ");
    expect(sandbox).not.toContain("allow-top-navigation");
    expect(sandbox).not.toContain("allow-top-navigation-by-user-activation");
    expect(sandbox).toContain("allow-scripts");
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
  });

  it("always says why it may be empty and opens the file in Drive, in a new tab", () => {
    render(<DrivePreview link={ID} />);

    expect(
      screen.getByText(/Google 계정으로 이\s+브라우저에 로그인/),
    ).toBeTruthy();
    const open = screen.getByRole("link", { name: "Drive에서 열기" });
    expect(open.getAttribute("href")).toBe(
      `https://drive.google.com/file/d/${ID}/view`,
    );
    expect(open.getAttribute("target")).toBe("_blank");
    expect(open.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it.each([
    ["another site", `https://example.com/file/d/${ID}/preview`],
    ["a script address", "javascript:alert(1)"],
    ["nothing", ""],
  ])("frames nothing for %s, and says so", (_what, link) => {
    const { container } = render(<DrivePreview link={link} />);

    expect(container.querySelector("iframe")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain(
      "Google Drive 파일의 링크가 아닙니다",
    );
  });
});
