import { render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { BookCover } from "@/components/BookCover";

vi.mock("next/image", () => ({
  default: ({ alt, src }: { alt: string; src: string }) => <div role="img" aria-label={alt} data-src={src} />,
}));

describe("BookCover", () => {
  test("resets to the new ISBN when book props change", () => {
    const { rerender } = render(<BookCover isbn="9780000000001" title="First book" />);
    expect(screen.getByRole("img", { name: "Cover of First book" }).getAttribute("data-src")).toContain("9780000000001");
    rerender(<BookCover isbn="9780000000002" title="Second book" />);
    expect(screen.getByRole("img", { name: "Cover of Second book" }).getAttribute("data-src")).toContain("9780000000002");
  });
});
