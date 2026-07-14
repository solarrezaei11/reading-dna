import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import PredictBook from "@/components/PredictBook";

const { predict } = vi.hoisted(() => ({ predict: vi.fn() }));

vi.mock("@/lib/api", () => ({
  api: { predict },
  apiErrorMessage: () => "Prediction failed",
}));

vi.mock("next/image", () => ({
  default: ({ alt, src }: { alt: string; src: string }) => <div role="img" aria-label={alt} data-src={src} />,
}));

describe("PredictBook", () => {
  beforeEach(() => {
    predict.mockReset();
  });

  test("passes an optional author and renders backend warnings", async () => {
    predict.mockResolvedValue({
      already_read: false,
      book: { title: "The Remains of the Day", author: "Kazuo Ishiguro" },
      predictions: {},
      warnings: ["Open Library was unavailable; prediction used the supplied title and author."],
    });
    render(<PredictBook dna={{}} books={[]} />);

    fireEvent.change(screen.getByLabelText("Book title to predict"), { target: { value: "The Remains of the Day" } });
    fireEvent.change(screen.getByLabelText("Book author to disambiguate prediction"), { target: { value: "Kazuo Ishiguro" } });
    fireEvent.click(screen.getByRole("button", { name: "Predict" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Open Library was unavailable");
    expect(predict).toHaveBeenCalledWith("The Remains of the Day", "Kazuo Ishiguro", {}, [], expect.any(AbortSignal));
  });
});
