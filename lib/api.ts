import type {
  BattleResult,
  Book,
  DnaProfile,
  JudgeResponse,
  LibbyResponse,
  MapData,
  PredictResponse,
} from "./types";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type ApiErrorKind = "unavailable" | "timeout" | "validation" | "model" | "http" | "invalid-response";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly kind: ApiErrorKind,
    public readonly status?: number,
    public readonly detail?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type RequestOptions = Omit<RequestInit, "body" | "signal"> & {
  body?: BodyInit | object;
  signal?: AbortSignal;
  timeoutMs?: number;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeDetail(value: unknown): string | undefined {
  if (typeof value === "string") return value.slice(0, 500);
  if (isRecord(value)) {
    if (typeof value.detail === "string") return value.detail.slice(0, 500);
    if (typeof value.message === "string") return value.message.slice(0, 500);
    if (typeof value.error === "string") return value.error.slice(0, 500);
  }
  return undefined;
}

function errorKind(status: number, detail?: string): ApiErrorKind {
  if (status === 400 || status === 422) return "validation";
  if (status === 502 || status === 503 || status === 504) return "unavailable";
  if (/model|ollama|llm/i.test(detail ?? "")) return "model";
  return "http";
}

export function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "The service could not be reached. Check your connection and try again.";
}

export function judgeFailureMessage(response: JudgeResponse): string | null {
  const entries = Object.values(response.judge);
  if (!entries.length) return "The judge returned no verdicts.";
  const errors = entries.map((entry) => entry.error).filter((error): error is string => Boolean(error));
  return errors.length === entries.length ? errors.join(" ") : null;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, signal, timeoutMs = 60_000, headers, ...init } = options;
  const controller = new AbortController();
  let timedOut = false;
  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });

  const isFormData = body instanceof FormData;
  const requestBody = body && !isFormData && typeof body === "object" ? JSON.stringify(body) : body;
  const requestHeaders = new Headers(headers);
  if (requestBody && !isFormData && !requestHeaders.has("Content-Type")) {
    requestHeaders.set("Content-Type", "application/json");
  }

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      body: requestBody,
      headers: requestHeaders,
      signal: controller.signal,
    });
    const text = await response.text();
    let payload: unknown;
    try {
      payload = text ? JSON.parse(text) : undefined;
    } catch {
      payload = text;
    }
    if (!response.ok) {
      const detail = safeDetail(payload);
      const kind = errorKind(response.status, detail);
      const fallback = kind === "validation"
        ? "The submitted data is invalid."
        : kind === "unavailable"
          ? "The service is temporarily unavailable."
          : `Request failed (${response.status}).`;
      throw new ApiError(detail ?? fallback, kind, response.status, detail);
    }
    if (!isRecord(payload)) {
      throw new ApiError("The service returned an invalid response.", "invalid-response", response.status);
    }
    return payload as T;
  } catch (error) {
    if (timedOut) throw new ApiError("The request timed out. Please try again.", "timeout");
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("The service could not be reached. Check your connection and try again.", "unavailable");
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

export const api = {
  parseCsv(file: File, signal?: AbortSignal) {
    const form = new FormData();
    form.append("file", file);
    return apiRequest<{ books: Book[]; count: number; warnings?: string[] }>("/parse/csv", {
      method: "POST", body: form, signal, timeoutMs: 60_000,
    });
  },
  parseRss(profileUrl: string, signal?: AbortSignal) {
    return apiRequest<{ books: Book[]; currently_reading?: Book[]; dnf?: Book[]; want_to_read?: Book[]; count: number; warnings?: string[] }>("/parse/rss", {
      method: "POST", body: { profile_url: profileUrl }, signal, timeoutMs: 90_000,
    });
  },
  dna(books: Book[], currentlyReading: Book[], dnf: Book[], signal?: AbortSignal) {
    return apiRequest<DnaProfile>("/dna", {
      method: "POST", body: { books, currently_reading: currentlyReading, dnf }, signal, timeoutMs: 90_000,
    });
  },
  battle(dnaProfile: DnaProfile, books: Book[], currentlyReading: Book[], dnf: Book[], wantToRead: Book[], signal?: AbortSignal) {
    return apiRequest<BattleResult>("/battle", {
      method: "POST",
      body: { dna_profile: dnaProfile, books, currently_reading: currentlyReading, dnf, want_to_read: wantToRead },
      signal,
      timeoutMs: 180_000,
    });
  },
  embeddings(books: Book[], recommendations: unknown[], signal?: AbortSignal) {
    return apiRequest<MapData>("/embeddings", {
      method: "POST", body: { books, recommendations }, signal, timeoutMs: 120_000,
    });
  },
  libby(isbns: string[], libraryName: string, signal?: AbortSignal) {
    return apiRequest<LibbyResponse>("/libby", {
      method: "POST", body: { isbns, library_name: libraryName }, signal, timeoutMs: 45_000,
    });
  },
  judge(dnaProfile: DnaProfile, battleResults: BattleResult, signal?: AbortSignal) {
    return apiRequest<JudgeResponse>("/judge", {
      method: "POST", body: { dna_profile: dnaProfile, battle_results: battleResults }, signal, timeoutMs: 300_000,
    });
  },
  predict(title: string, author: string | undefined, dnaProfile: DnaProfile, books: Book[], signal?: AbortSignal) {
    return apiRequest<PredictResponse>("/predict", {
      method: "POST", body: { title, ...(author ? { author } : {}), dna_profile: dnaProfile, books }, signal, timeoutMs: 60_000,
    });
  },
};
