import { describe, expect, test, vi } from "vitest";
import { api, apiRequest, judgeFailureMessage } from "@/lib/api";

describe("API handling", () => {
  test("surfaces backend detail from validation errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "No books provided" }), { status: 400 })));
    await expect(apiRequest("/dna")).rejects.toMatchObject({ kind: "validation", message: "No books provided" });
  });

  test("treats an all-error judge response as a retryable error", () => {
    expect(judgeFailureMessage({ judge: { "Model A": { error: "Ollama unavailable" } } })).toBe("Ollama unavailable");
    expect(judgeFailureMessage({ judge: { "Model A": { scores: { relevance: 8 } } } })).toBeNull();
  });

  test("sends the optional prediction author for book disambiguation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      already_read: false,
      book: { title: "The Remains of the Day" },
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await api.predict("The Remains of the Day", "Kazuo Ishiguro", {}, []);

    const [, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(request.body as string)).toMatchObject({
      title: "The Remains of the Day",
      author: "Kazuo Ishiguro",
    });
  });
});
