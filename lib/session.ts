import type { AnalysisInput, Book } from "./types";

export const ANALYSIS_INPUT_KEY = "reading-dna:analysis-input:v1";
const LEGACY_KEYS = ["books", "currently_reading", "dnf", "want_to_read", "library"] as const;

function isBook(value: unknown): value is Book {
  return typeof value === "object" && value !== null && typeof (value as { title?: unknown }).title === "string";
}

function books(value: unknown): Book[] {
  return Array.isArray(value) ? value.filter(isBook) : [];
}

function warnings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((warning): warning is string => typeof warning === "string") : [];
}

export function normalizeAnalysisInput(value: unknown): AnalysisInput | null {
  if (typeof value !== "object" || value === null) return null;
  const candidate = value as Record<string, unknown>;
  const source = candidate.source === "csv" || candidate.source === "rss" ? candidate.source : null;
  const parsedBooks = books(candidate.books);
  if (candidate.version !== 1 || !source || parsedBooks.length === 0) return null;
  return {
    version: 1,
    source,
    books: parsedBooks,
    currentlyReading: books(candidate.currentlyReading),
    dnf: books(candidate.dnf),
    wantToRead: books(candidate.wantToRead),
    library: typeof candidate.library === "string" ? candidate.library : "",
    warnings: warnings(candidate.warnings),
  };
}

export function createAnalysisInput(
  source: AnalysisInput["source"],
  input: Omit<AnalysisInput, "version" | "source">,
): AnalysisInput {
  return {
    version: 1,
    source,
    books: books(input.books),
    currentlyReading: source === "rss" ? books(input.currentlyReading) : [],
    dnf: source === "rss" ? books(input.dnf) : [],
    wantToRead: source === "rss" ? books(input.wantToRead) : [],
    library: input.library.trim(),
    warnings: warnings(input.warnings),
  };
}

function parseJson(value: string | null): unknown {
  if (!value) return undefined;
  try {
    return JSON.parse(value);
  } catch {
    return undefined;
  }
}

export function readAnalysisInput(storage: Storage): AnalysisInput | null {
  const current = normalizeAnalysisInput(parseJson(storage.getItem(ANALYSIS_INPUT_KEY)));
  if (current) return current;
  const legacyBooks = books(parseJson(storage.getItem("books")));
  if (!legacyBooks.length) return null;
  return createAnalysisInput("rss", {
    books: legacyBooks,
    currentlyReading: books(parseJson(storage.getItem("currently_reading"))),
    dnf: books(parseJson(storage.getItem("dnf"))),
    wantToRead: books(parseJson(storage.getItem("want_to_read"))),
    library: storage.getItem("library") ?? "",
  });
}

export function writeAnalysisInput(storage: Storage, input: AnalysisInput): void {
  storage.setItem(ANALYSIS_INPUT_KEY, JSON.stringify(input));
  LEGACY_KEYS.forEach((key) => storage.removeItem(key));
}

export function validateCsvFile(file: File, maxBytes = 10 * 1024 * 1024): string | null {
  const nameIsCsv = /\.csv$/i.test(file.name);
  const allowedType = !file.type || ["text/csv", "application/csv", "text/plain", "application/vnd.ms-excel"].includes(file.type);
  if (!nameIsCsv || !allowedType) return "Choose a CSV export file.";
  if (file.size === 0) return "The CSV file is empty.";
  if (file.size > maxBytes) return "The CSV file must be 10 MB or smaller.";
  return null;
}
