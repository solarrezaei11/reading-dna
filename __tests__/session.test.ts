import { describe, expect, test } from "vitest";
import { createAnalysisInput, readAnalysisInput, writeAnalysisInput } from "@/lib/session";

class MemoryStorage implements Storage {
  private values = new Map<string, string>();
  get length() { return this.values.size; }
  clear() { this.values.clear(); }
  getItem(key: string) { return this.values.get(key) ?? null; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  removeItem(key: string) { this.values.delete(key); }
  setItem(key: string, value: string) { this.values.set(key, value); }
}

describe("analysis session", () => {
  test("CSV input clears RSS-only shelves and legacy keys", () => {
    const storage = new MemoryStorage();
    storage.setItem("currently_reading", JSON.stringify([{ title: "Old shelf book" }]));
    const input = createAnalysisInput("csv", {
      books: [{ title: "Imported CSV book" }],
      currentlyReading: [{ title: "Should clear" }],
      dnf: [{ title: "Should clear" }],
      wantToRead: [{ title: "Should clear" }],
      library: " Library ",
    });
    writeAnalysisInput(storage, input);

    expect(readAnalysisInput(storage)).toMatchObject({
      source: "csv",
      currentlyReading: [],
      dnf: [],
      wantToRead: [],
      library: "Library",
    });
    expect(storage.getItem("currently_reading")).toBeNull();
  });

  test("falls back safely from corrupted versioned storage to a legacy session", () => {
    const storage = new MemoryStorage();
    storage.setItem("reading-dna:analysis-input:v1", "{not-json");
    storage.setItem("books", JSON.stringify([{ title: "Legacy book" }]));
    expect(readAnalysisInput(storage)?.books[0].title).toBe("Legacy book");
  });

  test("preserves partial-import warnings in the versioned session", () => {
    const storage = new MemoryStorage();
    writeAnalysisInput(storage, createAnalysisInput("rss", {
      books: [{ title: "Imported book" }],
      currentlyReading: [],
      dnf: [],
      wantToRead: [],
      library: "",
      warnings: ["Only the first 100 shelf entries were available."],
    }));

    expect(readAnalysisInput(storage)?.warnings).toEqual(["Only the first 100 shelf entries were available."]);
  });
});
