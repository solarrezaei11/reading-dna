"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import HimalayanCat from "@/components/HimalayanCat";
import { api, apiErrorMessage } from "@/lib/api";
import { createAnalysisInput, validateCsvFile, writeAnalysisInput } from "@/lib/session";

export default function Home() {
  const router = useRouter();
  const [tab, setTab] = useState<"csv" | "rss">("rss");
  const [rssUrl, setRssUrl] = useState("");
  const [library, setLibrary] = useState("");
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingSource, setLoadingSource] = useState<"csv" | "rss" | null>(null);
  const [error, setError] = useState("");
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => () => requestRef.current?.abort(), []);

  const handleFile = useCallback(async (file: File) => {
    if (loading) return;
    const validationError = validateCsvFile(file);
    if (validationError) {
      requestRef.current?.abort();
      setLoading(false);
      setLoadingSource(null);
      setError(validationError);
      return;
    }
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true);
    setLoadingSource("csv");
    setError("");
    try {
      const data = await api.parseCsv(file, controller.signal);
      if (!data.books.length) throw new Error("No books were found in that CSV export.");
      writeAnalysisInput(sessionStorage, createAnalysisInput("csv", {
        books: data.books, currentlyReading: [], dnf: [], wantToRead: [], library, warnings: data.warnings,
      }));
      router.push("/analyze");
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      setError(e instanceof Error && e.message.startsWith("No books") ? e.message : apiErrorMessage(e));
      setLoading(false);
    }
  }, [library, loading, router]);

  const handleRSS = useCallback(async () => {
    if (loading || !rssUrl) return;
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true);
    setLoadingSource("rss");
    setError("");
    try {
      const data = await api.parseRss(rssUrl, controller.signal);
      if (!data.books.length) throw new Error("No rated books found. Make sure your read shelf is public.");
      writeAnalysisInput(sessionStorage, createAnalysisInput("rss", {
        books: data.books,
        currentlyReading: data.currently_reading ?? [],
        dnf: data.dnf ?? [],
        wantToRead: data.want_to_read ?? [],
        library,
        warnings: data.warnings,
      }));
      router.push("/analyze");
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      setError(e instanceof Error && e.message.startsWith("No rated") ? e.message : apiErrorMessage(e));
      setLoading(false);
    }
  }, [library, loading, router, rssUrl]);

  const inputCls = [
    "w-full rounded-xl px-4 py-3 text-sm transition-colors focus:outline-none",
    "border focus:border-[color:var(--sage)]",
  ].join(" ");

  return (
    <main className="min-h-screen flex flex-col items-center justify-center px-4 py-16"
      style={{ background: "var(--bg)" }}>
      <div className="max-w-md w-full space-y-8">

        {/* Header */}
        <div className="text-center space-y-3">
          {/* Himalayan cat mascot */}
          <div className="flex justify-center mb-1">
            <HimalayanCat />
          </div>

          <h1 className="text-4xl tracking-tight leading-none">
            <span className="font-light" style={{ color: "var(--text-1)" }}>Reading</span>
            <span style={{ fontFamily: "var(--font-dm-serif)", color: "var(--sage)", fontStyle: "italic" }}>DNA</span>
          </h1>
          <p className="text-base leading-relaxed" style={{ color: "var(--text-2)" }}>
            Which AI knows you best as a reader?
          </p>
          <p className="text-sm max-w-xs mx-auto leading-relaxed" style={{ color: "var(--text-3)" }}>
            Import your Goodreads history. Two AI models compete to recommend books you&apos;ll love — you see who wins.
          </p>
        </div>

        {/* Card */}
        <div
          className="rounded-2xl p-6 space-y-5"
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border-mid)",
            boxShadow: "0 2px 16px rgba(139,107,70,0.06)",
          }}
        >
          {/* Tabs */}
          <div className="flex gap-1 rounded-xl p-1" style={{ background: "var(--surface-2)" }}>
            {(["rss", "csv"] as const).map((t) => (
              <button
                key={t}
                onClick={() => { setTab(t); setError(""); }}
                className="flex-1 py-2 text-sm rounded-lg transition-all font-medium"
                style={
                  tab === t
                    ? { background: "var(--surface)", color: "var(--sage-dark)", border: "1px solid var(--border-mid)", boxShadow: "0 1px 4px rgba(139,107,70,0.10)" }
                    : { color: "var(--text-3)", background: "transparent", border: "1px solid transparent" }
                }
              >
                {t === "csv" ? "CSV Export" : "Profile URL"}
              </button>
            ))}
          </div>

          {tab === "csv" ? (
            <div>
              <p className="text-xs mb-3" style={{ color: "var(--text-3)" }}>
                On Goodreads: <span style={{ color: "var(--text-2)" }}>My Books → Import/Export → Export Library</span>
              </p>
              <label
                onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault(); setDragging(false);
                  const file = e.dataTransfer.files[0]; if (!loading && file) handleFile(file);
                }}
                className={`flex flex-col items-center justify-center gap-3 rounded-xl p-10 transition-colors ${loading ? "cursor-not-allowed" : "cursor-pointer"}`}
                style={{
                  border: `2px dashed ${dragging ? "var(--sage)" : "var(--border-mid)"}`,
                  background: dragging ? "var(--sage-pale)" : "transparent",
                }}
              >
                <span className="text-2xl select-none">📖</span>
                <span className="text-sm" style={{ color: "var(--text-2)" }}>Drop your CSV here or click to browse</span>
                <input type="file" accept=".csv" className="hidden" disabled={loading}
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }} />
              </label>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-xs" style={{ color: "var(--text-3)" }}>
                Your profile must be <span style={{ color: "var(--text-2)" }}>public</span>. Paste your Goodreads profile URL.
              </p>
              <input
                type="url"
                value={rssUrl}
                onChange={(e) => setRssUrl(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleRSS()}
                placeholder="https://www.goodreads.com/user/show/12345678-name"
                className={inputCls}
                style={{ background: "var(--surface-2)", borderColor: "var(--border-mid)", color: "var(--text-1)" }}
              />
              <button
                onClick={handleRSS}
                disabled={!rssUrl || loading}
                className="w-full rounded-xl py-3 text-sm font-medium transition-all"
                style={{
                  background: !rssUrl || loading ? "var(--surface-2)" : "var(--sage)",
                  color: !rssUrl || loading ? "var(--text-3)" : "#fdfaf5",
                  border: `1px solid ${!rssUrl || loading ? "var(--border)" : "var(--sage-dark)"}`,
                  cursor: !rssUrl || loading ? "not-allowed" : "pointer",
                  boxShadow: !rssUrl || loading ? "none" : "0 2px 8px rgba(90,138,90,0.25)",
                }}
              >
                {loading ? "Fetching your shelves…" : "Analyze my reading →"}
              </button>
            </div>
          )}

          <div className="space-y-2">
            <label className="text-xs" style={{ color: "var(--text-3)" }}>
              Library system <span style={{ opacity: 0.7 }}>(optional — check Libby)</span>
            </label>
            <input
              type="text"
              value={library}
              onChange={(e) => setLibrary(e.target.value)}
              placeholder="Toronto Public Library or libbyapp.com/library/toronto"
              className={inputCls}
              style={{ background: "var(--surface-2)", borderColor: "var(--border)", color: "var(--text-1)" }}
            />
          </div>

          {error && (
            <p role="alert" className="text-sm rounded-xl px-3 py-2.5" style={{ color: "var(--rust)", background: "rgba(176,90,69,0.07)", border: "1px solid rgba(176,90,69,0.18)" }}>
              {error}
            </p>
          )}
        </div>

        {/* Feature pills */}
        <div className="flex flex-wrap justify-center gap-2">
          {["Reading DNA Profile", "AI Model Battle", "Reading Universe Map", "Libby Availability", "Shareable Card"].map((f) => (
            <span
              key={f}
              className="text-xs px-3 py-1 rounded-full"
              style={{ color: "var(--text-3)", background: "var(--surface)", border: "1px solid var(--border)" }}
            >
              {f}
            </span>
          ))}
        </div>

      </div>

      {/* Loading overlay */}
      {loading && (
        <div className="fixed inset-0 flex items-center justify-center z-50"
          style={{ background: "rgba(247,242,235,0.85)", backdropFilter: "blur(6px)" }}>
          <div className="text-center space-y-4">
            <div className="w-8 h-8 rounded-full border-2 border-t-transparent animate-spin mx-auto"
              style={{ borderColor: "var(--sage) transparent var(--sage) var(--sage)" }} />
            <p className="text-sm" style={{ color: "var(--text-2)" }}>
              {loadingSource === "csv" ? "Importing your CSV…" : "Fetching your Goodreads shelves…"}
            </p>
          </div>
        </div>
      )}
    </main>
  );
}
