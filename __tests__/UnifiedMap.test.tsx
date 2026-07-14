import { render, screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import UnifiedMap from "@/components/UnifiedMap";

vi.mock("next/image", () => ({
  default: ({ alt, src }: { alt: string; src: string }) => <div role="img" aria-label={alt} data-src={src} />,
}));

describe("UnifiedMap recommendation list", () => {
  test("renders accessible recommendation details and Libby .results availability", () => {
    render(
      <UnifiedMap
        mapData={null}
        library="Example Library"
        libbyData={{ library_found: true, matched_library_name: "Example Library", results: { "9781": { available: true } } }}
        battle={{
          models: {
            "Model A": {
              recommendations: [{
                title: "Accessible Book", author: "A. Author", isbn: "9781", why: "A precise match",
                on_tbr: true, hidden_gem: true, comfort_zone: false,
              }],
            },
          },
        }}
      />,
    );
    expect(screen.getByRole("heading", { name: "Recommendations" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Accessible Book" })).toBeInTheDocument();
    expect(screen.getByText("Available on Libby")).toBeInTheDocument();
    expect(screen.getByText("Hidden gem")).toBeInTheDocument();
    expect(screen.getByText("Outside comfort zone")).toBeInTheDocument();
  });

  test("does not infer a comfort-zone badge when the backend omits it", () => {
    const { container } = render(
      <UnifiedMap
        mapData={null}
        battle={{ models: { "Model A": { recommendations: [{ title: "Unlabeled Book", author: "A. Author" }] } } }}
      />,
    );

    expect(within(container).queryByText("Comfort-zone fit")).not.toBeInTheDocument();
    expect(within(container).queryByText("Outside comfort zone")).not.toBeInTheDocument();
  });

  test("explains that Libby cannot check availability without recommendation ISBNs", () => {
    render(
      <UnifiedMap
        mapData={null}
        library="Example Library"
        libbyData={{
          library_found: false,
          skipped_reason: "no_isbns",
          results: {},
          warnings: ["Availability could not be checked because the recommendations did not include verified ISBNs."],
        }}
        battle={{ models: { "Model A": { recommendations: [{ title: "No ISBN Book" }] } } }}
      />,
    );

    expect(screen.getByText("Availability could not be checked because the recommendations did not include verified ISBNs.")).toBeInTheDocument();
    expect(screen.queryByText(/We could not match/)).not.toBeInTheDocument();
  });

  test("renders a reported Libby wait estimate", () => {
    render(
      <UnifiedMap
        mapData={null}
        library="Example Library"
        libbyData={{
          library_found: true,
          results: { "9781": { available: false, status: "waitlist", wait_weeks: 2 } },
        }}
        battle={{
          models: {
            "Model A": {
              recommendations: [{ title: "Waitlisted Book", author: "A. Author", isbn: "9781" }],
            },
          },
        }}
      />,
    );

    expect(screen.getByText("Libby waitlist: about 2 weeks")).toBeInTheDocument();
  });

  test("distinguishes a judge tie from a missing winner", () => {
    render(
      <UnifiedMap
        mapData={null}
        battle={{
          models: {
            "Model A": { recommendations: [{ title: "Book A" }] },
            "Model B": { recommendations: [{ title: "Book B" }] },
          },
          judge: {
            "Model A": { scores: { relevance: 8 }, verdict: "Good fit." },
            "Model B": { scores: { relevance: 8 }, verdict: "Good fit." },
          },
          winner: null,
          tie: true,
        }}
      />,
    );

    expect(screen.getByText("The judge scored this as a tie.")).toBeInTheDocument();
  });
});
