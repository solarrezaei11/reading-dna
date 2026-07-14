import { describe, expect, test } from "vitest";
import { normalizeMapRecommendations, normalizeRecommendations } from "@/lib/recommendations";

describe("recommendation normalization", () => {
  test("merges editions by canonical title and author while preserving both ISBN metadata sources", () => {
    const recommendations = normalizeRecommendations({
      "Model A": { recommendations: [{ title: " Beloved ", author: "Toni Morrison", isbn: "9781400033416", why: "Literary depth", on_tbr: true }] },
      "Model B": { recommendations: [{ title: "beloved", author: "  TONI   MORRISON ", isbn: "9780593689680", why: "Emotional resonance", hidden_gem: true, comfort_zone: false }] },
    });
    expect(recommendations).toHaveLength(1);
    expect(recommendations[0]).toMatchObject({
      isConsensus: true,
      models: ["Model A", "Model B"],
      on_tbr: true,
      hidden_gem: true,
      comfort_zone: false,
      reasons: { "Model A": "Literary depth", "Model B": "Emotional resonance" },
    });
    expect(recommendations[0].isbn).toBe("9781400033416");
  });

  test("retains true when the other consensus entry omits comfort zone", () => {
    const recommendations = normalizeRecommendations({
      "Model A": { recommendations: [{ title: "Comfortable", author: "Author", comfort_zone: true }] },
      "Model B": { recommendations: [{ title: "Comfortable", author: "Author" }] },
    });

    expect(recommendations[0].comfort_zone).toBe(true);
  });

  test("gives false precedence over true across consensus entries", () => {
    const recommendations = normalizeRecommendations({
      "Model A": { recommendations: [{ title: "Stretch", author: "Author", comfort_zone: true }] },
      "Model B": { recommendations: [{ title: "Stretch", author: "Author", comfort_zone: false }] },
    });

    expect(recommendations[0].comfort_zone).toBe(false);
  });

  test("fills missing ISBN and year from a consensus entry for Libby checks", () => {
    const recommendations = normalizeRecommendations({
      "Model A": { recommendations: [{ title: "The Left Hand of Darkness", author: "Ursula K. Le Guin", why: "First pick" }] },
      "Model B": { recommendations: [{ title: "The Left Hand of Darkness", author: "Ursula K. Le Guin", isbn: "9780441478125", year: 1969, why: "Verified edition" }] },
    });

    expect(recommendations).toHaveLength(1);
    expect(recommendations[0]).toMatchObject({
      isbn: "9780441478125",
      year: 1969,
      reasons: { "Model A": "First pick", "Model B": "Verified edition" },
    });
  });

  test("normalizes Unicode compatibility forms deterministically", () => {
    const recommendations = normalizeRecommendations({
      "Model A": { recommendations: [{ title: "Ｂｅｌｏｖｅｄ", author: "Ｔｏｎｉ Ｍｏｒｒｉｓｏｎ" }] },
      "Model B": { recommendations: [{ title: "Beloved", author: "Toni Morrison" }] },
    });

    expect(recommendations).toHaveLength(1);
    expect(recommendations[0].isConsensus).toBe(true);
  });

  test("omits unmatched map recommendations instead of borrowing another book's coordinates", () => {
    const normalized = normalizeRecommendations({
      "Model A": { recommendations: [{ title: "Unmapped", author: "Author" }] },
    });
    const mapped = normalizeMapRecommendations([{
      title: "Mapped", author: "Other Author", model_name: "Model A", x: 0.2, y: 0.4,
    }], normalized);

    expect(mapped).toEqual([]);
  });
});
