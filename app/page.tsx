"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const DEFAULT_RSS = "";
const DEFAULT_LIBRARY = "";

export default function Home() {
  const router = useRouter();
  const [tab, setTab] = useState<"csv" | "rss">("rss");
  const [rssUrl, setRssUrl] = useState(DEFAULT_RSS);
  const [library, setLibrary] = useState(DEFAULT_LIBRARY);
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

  const handleRSSWithValues = async (url: string, lib: string) => {
    if (!url) return;
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API}/parse/rss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile_url: url }),
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
      sessionStorage.setItem("library", lib);
      router.push("/analyze");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      setLoading(false);
    }
  };

  const handleRSS = () => handleRSSWithValues(rssUrl, library);

  return (
    <main className="min-h-screen flex flex-col items-center justify-center px-4 py-16">
      <div className="max-w-xl w-full space-y-10">

        {/* Header */}
        <div className="text-center space-y-3">
          <div className="inline-block text-xs font-mono tracking-widest text-purple-400 bg-purple-400/10 border border-purple-400/20 px-3 py-1 rounded-full">
            BETA
          </div>
          <h1 className="text-4xl font-bold tracking-tight">
            Reading<span className="text-purple-400">DNA</span>
          </h1>
          <p className="text-zinc-400 text-lg">
            Which AI knows you best as a reader?
          </p>
          <p className="text-zinc-500 text-sm max-w-md mx-auto">
            Import your Goodreads history. We build your taste profile, then
            two AI models compete to recommend books — and you see who knows you better.
          </p>
        </div>

        {/* Card */}
        <div className="bg-white/5 border border-white/10 rounded-2xl p-6 space-y-5">

          {/* Tabs */}
          <div className="flex gap-1 bg-white/5 rounded-lg p-1">
            {(["csv", "rss"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`flex-1 py-1.5 text-sm rounded-md transition-colors font-medium ${
                  tab === t ? "bg-purple-600 text-white" : "text-zinc-400 hover:text-white"
                }`}
              >
                {t === "csv" ? "CSV Export" : "Goodreads Profile URL"}
              </button>
            ))}
          </div>

          {tab === "csv" ? (
            <div>
              <p className="text-xs text-zinc-500 mb-3">
                On Goodreads: <span className="text-zinc-300">My Books → Import/Export → Export Library</span>
              </p>
              <label
                onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  const file = e.dataTransfer.files[0];
                  if (file) handleFile(file);
                }}
                className={`flex flex-col items-center justify-center gap-2 border-2 border-dashed rounded-xl p-10 cursor-pointer transition-colors ${
                  dragging ? "border-purple-400 bg-purple-400/10" : "border-white/20 hover:border-white/40"
                }`}
              >
                <span className="text-3xl">📚</span>
                <span className="text-sm text-zinc-400">Drop your CSV here or click to browse</span>
                <input
                  type="file"
                  accept=".csv"
                  className="hidden"
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
                />
              </label>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-xs text-zinc-500">
                Your profile must be <span className="text-zinc-300">public</span>. Paste your Goodreads profile URL.
              </p>
              <input
                type="url"
                value={rssUrl}
                onChange={(e) => setRssUrl(e.target.value)}
                placeholder="https://www.goodreads.com/user/show/12345678-your-name"
                className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-2.5 text-sm placeholder:text-zinc-600 focus:outline-none focus:border-purple-500"
              />
              <button
                onClick={handleRSS}
                disabled={!rssUrl || loading}
                className="w-full bg-purple-600 hover:bg-purple-700 disabled:opacity-40 rounded-lg py-2.5 text-sm font-medium transition-colors"
              >
                {loading ? "Fetching..." : "Import via RSS"}
              </button>
            </div>
          )}

          {/* Library input */}
          <div className="space-y-1.5">
            <label className="text-xs text-zinc-400">
              Library system (optional — for Libby availability)
            </label>
            <input
              type="text"
              value={library}
              onChange={(e) => setLibrary(e.target.value)}
              placeholder="e.g. Seattle Public Library"
              className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-2.5 text-sm placeholder:text-zinc-600 focus:outline-none focus:border-purple-500"
            />
          </div>

          {error && (
            <p className="text-red-400 text-sm">{error}</p>
          )}
        </div>

        {/* Feature pills */}
        <div className="flex flex-wrap justify-center gap-2">
          {["Reading DNA Profile", "AI Model Battle", "Libby Availability", "Reading Universe Map", "Shareable Card"].map((f) => (
            <span key={f} className="text-xs text-zinc-500 bg-white/5 border border-white/10 px-3 py-1 rounded-full">
              {f}
            </span>
          ))}
        </div>

      </div>

      {loading && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="text-center space-y-3">
            <div className="w-8 h-8 border-2 border-purple-400 border-t-transparent rounded-full animate-spin mx-auto" />
            <p className="text-zinc-400 text-sm">Parsing your books...</p>
          </div>
        </div>
      )}
    </main>
  );
}
