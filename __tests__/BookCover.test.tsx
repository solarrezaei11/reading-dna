import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { BookCover } from "@/components/BookCover";

vi.mock("next/image", () => ({
  default: ({ alt, src }: { alt: string; src: string }) => <div role="img" aria-label={alt} data-src={src} />,
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BookCover", () => {
  test("resets to the new ISBN when book props change", () => {
    const { rerender } = render(<BookCover isbn="9780000000001" title="First book" />);
    expect(screen.getByRole("img", { name: "Cover of First book" }).getAttribute("data-src")).toContain("9780000000001");
    rerender(<BookCover isbn="9780000000002" title="Second book" />);
    expect(screen.getByRole("img", { name: "Cover of Second book" }).getAttribute("data-src")).toContain("9780000000002");
  });

  test("does not reuse a fallback cover when delimiter-containing identities collide", async () => {
    const pendingResponse = new Promise<Response>(() => undefined);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ docs: [{ cover_i: 101 }] }),
      })
      .mockImplementationOnce(() => pendingResponse);
    vi.stubGlobal("fetch", fetchMock);

    const { rerender } = render(<BookCover title="A|B" author="C" />);
    await waitFor(() => {
      expect(screen.getByRole("img", { name: "Cover of A|B" })).toBeInTheDocument();
    });

    rerender(<BookCover title="A" author="B|C" />);
    expect(screen.queryByRole("img", { name: "Cover of A" })).not.toBeInTheDocument();
  });
});
