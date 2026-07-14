import { afterEach, describe, expect, test, vi } from "vitest";
import { siteUrl } from "@/lib/site";

afterEach(() => vi.unstubAllEnvs());

describe("site URL", () => {
  test("uses localhost rather than a dead deployment URL when unset", () => {
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "");
    expect(siteUrl().toString()).toBe("http://localhost:3000/");
  });
});
