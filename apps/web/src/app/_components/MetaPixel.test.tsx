import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MetaPixel } from "./MetaPixel";

// next/script loads nothing in jsdom; draw what it was given so the test can
// read the id and the inline code.
vi.mock("next/script", () => ({
  default: ({ id, children }: { id: string; children: string }) => (
    <script data-testid={id}>{children}</script>
  ),
}));

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

const script = (container: HTMLElement) => container.querySelector("script");

describe("MetaPixel", () => {
  it("draws nothing when no id is set", () => {
    const { container } = render(<MetaPixel pixelId="" />);

    expect(script(container)).toBeNull();
  });

  it("draws nothing for an id that is not digits, which would be written into a script", () => {
    for (const bad of ["abc", "123'); alert(1);//", "1234", "12345 6789"]) {
      const { container, unmount } = render(<MetaPixel pixelId={bad} />);
      expect(script(container)).toBeNull();
      unmount();
    }
  });

  it("starts the pixel with the given id on an address with no query", () => {
    const { container } = render(<MetaPixel pixelId="1234567890123456" />);

    const code = script(container)?.textContent ?? "";
    expect(code).toContain("fbq('init','1234567890123456')");
    expect(code).toContain("fbq('track','PageView')");
  });

  it("draws nothing when the address carries a query, as /login?next= does", () => {
    window.history.replaceState(null, "", "/login?next=/meetings/abc");

    const { container } = render(<MetaPixel pixelId="1234567890123456" />);

    expect(script(container)).toBeNull();
  });
});
