"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import HimalayanCat from "@/components/HimalayanCat";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function Home() {
  const router = useRouter();
  const [tab, setTab] = useState<"csv" | "rss">("rss");
  const [rssUrl, setRssUrl] = useState("");
  const [library, setLibrary] = useState("");
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleFile = useCallback(async (file: File) => {
    setLoading(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${API}/parse/csv`, { method: "POST", body: form });
      if (!res.ok) throw new Error("Failed to parse CSV");
      const data = await res.json();
      sessionStorage.setItem("books", JSON.stringify(data.books));
      sessionStorage.setItem("library", library);
      router.push("/analyze");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      setLoading(false);
    }
  }, [router, library]);

  const handleRSS = async () => {
    if (!rssUrl) return;
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API}/parse/rss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile_url: rssUrl }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Could not fetch RSS — check your profile is public");
      }
      const data = await res.json();
      if (!data.books?.length) throw new Error("No rated books found. Make sure your 'read' shelf is public.");
      sessionStorage.setItem("books", JSON.stringify(data.books));
      sessionStorage.setItem("currently_reading", JSON.stringify(data.currently_reading || []));
      sessionStorage.setItem("dnf", JSON.stringify(data.dnf || []));
      sessionStorage.setItem("library", library);
      router.push("/analyze");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      setLoading(false);
    }
  };

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
            Import your Goodreads history. Two AI models compete to recommend books you'll love — you see who wins.
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
                onClick={() => setTab(t)}
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
                  const file = e.dataTransfer.files[0]; if (file) handleFile(file);
                }}
                className="flex flex-col items-center justify-center gap-3 rounded-xl p-10 cursor-pointer transition-colors"
                style={{
                  border: `2px dashed ${dragging ? "var(--sage)" : "var(--border-mid)"}`,
                  background: dragging ? "var(--sage-pale)" : "transparent",
                }}
              >
                <span className="text-2xl select-none">📖</span>
                <span className="text-sm" style={{ color: "var(--text-2)" }}>Drop your CSV here or click to browse</span>
                <input type="file" accept=".csv" className="hidden"
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
                placeholder="goodreads.com/user/show/12345678-name"
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
              placeholder="e.g. Toronto Public Library"
              className={inputCls}
              style={{ background: "var(--surface-2)", borderColor: "var(--border)", color: "var(--text-1)" }}
            />
          </div>

          {error && (
            <p className="text-sm rounded-xl px-3 py-2.5" style={{ color: "var(--rust)", background: "rgba(176,90,69,0.07)", border: "1px solid rgba(176,90,69,0.18)" }}>
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
            <p className="text-sm" style={{ color: "var(--text-2)" }}>Fetching your shelves…</p>
          </div>
        </div>
      )}
    </main>
  );
}
