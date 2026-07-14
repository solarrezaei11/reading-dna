"use client";

import { useEffect, useRef, useState } from "react";
import { BookCover } from "./BookCover";
import { api, apiErrorMessage } from "@/lib/api";
import { clamp } from "@/lib/recommendations";
import type { Book, DnaProfile, PredictResponse, Prediction } from "@/lib/types";

type Props = { dna: DnaProfile; books: Book[] };

function Stars({ value }: { value: number }) {
  return (
    <span className="tabular-nums">
      {value.toFixed(1)}
      <span style={{ color: "var(--sage)" }}> ★</span>
    </span>
  );
}

export default function PredictBook({ dna, books }: Props) {
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<PredictResponse | null>(null);
  const [error, setError] = useState("");
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => () => requestRef.current?.abort(), []);

  async function predict() {
    const q = title.trim();
    if (!q || loading) return;
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      setResult(await api.predict(q, author.trim() || undefined, dna, books, controller.signal));
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      setError(apiErrorMessage(e));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  const models: Array<[string, Prediction]> = result?.predictions ? Object.entries(result.predictions) : [];
  const ratings = models
    .map(([, p]) => p.predicted_rating)
    .filter((r): r is number => typeof r === "number");
  const agreement =
    ratings.length === 2 ? (Math.abs(ratings[0] - ratings[1]) <= 0.3 ? "agree" : "disagree") : null;

  return (
    <div
      className="rounded-2xl p-6 space-y-4"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
    >
      <div>
        <h2
          className="text-xl font-light tracking-tight"
          style={{ fontFamily: "var(--font-dm-serif)", color: "var(--text-1)", fontStyle: "italic" }}
        >
          Will I like it?
        </h2>
        <p className="text-xs mt-1" style={{ color: "var(--text-3)" }}>
          Type any book — two models predict your rating from your DNA and shelf history.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_minmax(0,0.7fr)_auto] gap-2">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && predict()}
          placeholder="e.g. The Remains of the Day"
          aria-label="Book title to predict"
          className="flex-1 min-w-0 rounded-xl px-3.5 py-2 text-sm outline-none transition-colors"
          style={{
            background: "var(--bg)",
            border: "1px solid var(--border-mid)",
            color: "var(--text-1)",
          }}
          onFocus={(e) => (e.currentTarget.style.borderColor = "var(--sage)")}
          onBlur={(e) => (e.currentTarget.style.borderColor = "var(--border-mid)")}
        />
        <input
          value={author}
          onChange={(e) => setAuthor(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && predict()}
          placeholder="Author (optional)"
          aria-label="Book author to disambiguate prediction"
          className="min-w-0 rounded-xl px-3.5 py-2 text-sm outline-none transition-colors"
          style={{
            background: "var(--bg)",
            border: "1px solid var(--border-mid)",
            color: "var(--text-1)",
          }}
          onFocus={(e) => (e.currentTarget.style.borderColor = "var(--sage)")}
          onBlur={(e) => (e.currentTarget.style.borderColor = "var(--border-mid)")}
        />
        <button
          onClick={predict}
          disabled={loading || !title.trim()}
          className="px-4 py-2 rounded-xl text-sm transition-all disabled:opacity-40"
          style={{ background: "var(--sage)", color: "#fdfaf0" }}
        >
          {loading ? "Predicting…" : "Predict"}
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-2.5 text-xs" style={{ color: "var(--text-3)" }}>
          <div
            className="w-3.5 h-3.5 border-2 border-t-transparent rounded-full animate-spin shrink-0"
            style={{ borderColor: "var(--sage) transparent var(--sage) var(--sage)" }}
          />
          Two models are reading your DNA…
        </div>
      )}

      {error && (
        <p className="text-xs" style={{ color: "var(--rust)" }}>{error}</p>
      )}

      {(result?.warnings?.length ?? 0) > 0 && (
        <p role="status" className="rounded-xl px-3 py-2 text-xs" style={{ color: "var(--text-2)", background: "var(--surface-2)", border: "1px solid var(--border)" }}>
          {[...new Set(result?.warnings ?? [])].join(" ")}
        </p>
      )}

      {result?.already_read && (
        <div
          className="flex items-center gap-3 rounded-xl p-4"
          style={{ background: "var(--bg)", border: "1px solid var(--border)" }}
        >
          <BookCover isbn={result.book.isbn} title={result.book.title} author={result.book.author} size={40} />
          <div className="text-sm" style={{ color: "var(--text-2)" }}>
            You&apos;ve already read <span className="font-medium" style={{ color: "var(--text-1)" }}>{result.book.title}</span>
            {result.actual_rating ? (
              <> — you gave it <Stars value={result.actual_rating} /></>
            ) : (
              <> — you didn&apos;t rate it.</>
            )}
          </div>
        </div>
      )}

      {result && !result.already_read && (
        <div className="space-y-4">
          {/* Resolved book */}
          <div className="flex items-center gap-3">
            <BookCover isbn={result.book.isbn} title={result.book.title} author={result.book.author} size={40} />
            <div className="min-w-0">
              <div className="text-sm font-medium truncate" style={{ color: "var(--text-1)" }}>
                {result.book.title}
              </div>
              <div className="text-xs" style={{ color: "var(--text-3)" }}>
                {result.book.author}
                {result.book.year ? ` · ${result.book.year}` : ""}
                {!result.resolved && " · (not found on Open Library — predicting from title alone)"}
              </div>
            </div>
          </div>

          {/* Model predictions */}
          <div className="grid sm:grid-cols-2 gap-3">
            {models.map(([name, p]) => (
              <div
                key={name}
                className="rounded-xl p-4 space-y-2"
                style={{ background: "var(--bg)", border: "1px solid var(--border)" }}
              >
                <div
                  className="text-[10px] tracking-[0.15em] uppercase"
                  style={{ color: "var(--text-3)", fontFamily: "var(--font-geist-mono)" }}
                >
                  {name}
                </div>
                {p.error ? (
                  <p className="text-xs" style={{ color: "var(--rust)" }}>{p.error}</p>
                ) : (
                  <>
                    <div className="text-3xl" style={{ fontFamily: "var(--font-dm-serif)", fontStyle: "italic", color: "var(--text-1)" }}>
                      <Stars value={p.predicted_rating ?? 0} />
                    </div>
                    {typeof p.confidence === "number" && (
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1 rounded-full overflow-hidden" style={{ background: "var(--border)" }}>
                          <div
                            className="h-full rounded-full"
                            style={{ width: `${Math.round(clamp(p.confidence, 0, 1) * 100)}%`, background: "var(--sage)" }}
                          />
                        </div>
                        <span className="text-[10px] tabular-nums" style={{ color: "var(--text-3)" }}>
                          {Math.round(clamp(p.confidence, 0, 1) * 100)}%
                        </span>
                      </div>
                    )}
                    <p className="text-xs leading-relaxed" style={{ color: "var(--text-2)" }}>{p.why}</p>
                    {(p.drivers || []).length > 0 && (
                      <div className="flex flex-wrap gap-1.5 pt-0.5">
                        {p.drivers?.map((d, i) => (
                          <span
                            key={i}
                            className="text-[10px] px-2 py-0.5 rounded-full"
                            style={
                              d.direction === "+"
                                ? { color: "var(--sage)", background: "rgba(90,138,90,0.10)", border: "1px solid rgba(90,138,90,0.25)" }
                                : { color: "var(--rust)", background: "rgba(180,100,70,0.08)", border: "1px solid rgba(180,100,70,0.22)" }
                            }
                          >
                            {d.direction === "+" ? "+" : "−"} {d.factor}
                          </span>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            ))}
          </div>

          {agreement && (
            <p className="text-xs" style={{ color: "var(--text-3)" }}>
              {agreement === "agree"
                ? "Both models agree on this one."
                : `The models disagree by ${Math.abs(ratings[0] - ratings[1]).toFixed(1)} stars — a genuinely uncertain pick.`}
            </p>
          )}

          {/* Shelf evidence */}
          {(result.neighbors || []).length > 0 && (
            <div className="space-y-1.5">
              <div
                className="text-[10px] tracking-[0.15em] uppercase"
                style={{ color: "var(--text-3)", fontFamily: "var(--font-geist-mono)" }}
              >
                Closest books on your shelf
              </div>
              <div className="flex flex-wrap gap-1.5">
                {result.neighbors?.map((n, i) => (
                  <span
                    key={i}
                    className="text-[11px] px-2.5 py-1 rounded-full"
                    style={{ color: "var(--text-2)", background: "var(--bg)", border: "1px solid var(--border)" }}
                    title={`similarity ${n.similarity}`}
                  >
                    {n.title}
                    {n.my_rating ? <span style={{ color: "var(--sage)" }}> {n.my_rating}★</span> : null}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Latency observability */}
          {result.stages && (
            <div
              className="text-[10px] pt-1"
              style={{ color: "var(--text-3)", fontFamily: "var(--font-geist-mono)", opacity: 0.75 }}
            >
              resolve {result.stages.resolve_ms}ms · embed {result.stages.embed_ms}ms · llm {result.stages.llm_ms}ms · total{" "}
              {result.stages.total_ms}ms
              {models.map(([name, p]) =>
                p.meta?.ttft_ms ? ` — ${name.split(" ")[0].toLowerCase()} ttft ${p.meta.ttft_ms}ms` : ""
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
