"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import DNAProfile from "@/components/DNAProfile";
import UnifiedMap from "@/components/UnifiedMap";
import ShareCard from "@/components/ShareCard";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Step = "dna" | "battle" | "map" | "done";

export default function AnalyzePage() {
  const router = useRouter();
  const [books, setBooks] = useState<any[]>([]);
  const [currentlyReadingCount, setCurrentlyReadingCount] = useState(0);
  const [dnfCount, setDnfCount] = useState(0);
  const [wantToReadCount, setWantToReadCount] = useState(0);
  const [library, setLibrary] = useState("");
  const [step, setStep] = useState<Step>("dna");
  const [dna, setDna] = useState<any>(null);
  const [battle, setBattle] = useState<any>(null);
  const [judgeData, setJudgeData] = useState<any>(null);
  const [judgeLoading, setJudgeLoading] = useState(false);
  const [judgeError, setJudgeError] = useState("");
  const [mapData, setMapData] = useState<any>(null);
  const [libbyData, setLibbyData] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const stored = sessionStorage.getItem("books");
    const lib = sessionStorage.getItem("library") || "";
    if (!stored) { router.push("/"); return; }
    const parsed = JSON.parse(stored);
    const currentlyReading = JSON.parse(sessionStorage.getItem("currently_reading") || "[]");
    const dnf = JSON.parse(sessionStorage.getItem("dnf") || "[]");
    const wantToRead = JSON.parse(sessionStorage.getItem("want_to_read") || "[]");
    setBooks(parsed);
    setCurrentlyReadingCount(currentlyReading.length);
    setDnfCount(dnf.length);
    setWantToReadCount(wantToRead.length);
    setLibrary(lib);
    runPipeline(parsed, lib, currentlyReading, dnf, wantToRead);
  }, []);

  async function runPipeline(books: any[], lib: string, currentlyReading: any[] = [], dnf: any[] = [], wantToRead: any[] = []) {
    try {
      // Step 1: DNA
      setStep("dna");
      const dnaRes = await fetch(`${API}/dna`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ books, currently_reading: currentlyReading, dnf }),
      });
      if (!dnaRes.ok) throw new Error("DNA analysis failed");
      const dnaData = await dnaRes.json();
      setDna(dnaData);

      // Step 2: Battle
      setStep("battle");
      const battleRes = await fetch(`${API}/battle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dna_profile: dnaData, books, currently_reading: currentlyReading, dnf, want_to_read: wantToRead }),
      });
      if (!battleRes.ok) throw new Error("LLM battle failed");
      const battleData = await battleRes.json();
      setBattle(battleData);

      // Judge is opt-in — triggered via runJudge() below

      // Check Libby if library provided
      if (lib) {
        const allRecs = Object.values(battleData.models as Record<string, any>)
          .flatMap((m: any) => m.recommendations || [])
          .map((r: any) => r.isbn)
          .filter(Boolean);
        const uniqueIsbns = [...new Set(allRecs)] as string[];
        if (uniqueIsbns.length > 0) {
          const libbyRes = await fetch(`${API}/libby`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ isbns: uniqueIsbns, library_name: lib }),
          });
          if (libbyRes.ok) {
            setLibbyData(await libbyRes.json());
          }
        }
      }

      // Step 3: Map — pass recs so they're embedded in the same space
      setStep("map");
      const allRecs = Object.values(battleData.models as Record<string, any>)
        .flatMap((m: any) => (m.recommendations || []).map((r: any) => ({
          ...r,
          model_name: Object.keys(battleData.models).find(k => battleData.models[k] === m),
        })));
      const mapRes = await fetch(`${API}/embeddings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ books, recommendations: allRecs }),
      });
      if (!mapRes.ok) throw new Error("Reading map failed");
      const mapD = await mapRes.json();
      setMapData(mapD);

      setStep("done");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    }
  }

  async function runJudge() {
    if (!dna || !battle || judgeLoading || judgeData) return;
    setJudgeLoading(true);
    setJudgeError("");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5 * 60 * 1000);
    try {
      const res = await fetch(`${API}/judge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dna_profile: dna, battle_results: battle }),
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setJudgeData(await res.json());
    } catch (e: unknown) {
      setJudgeError(e instanceof Error && e.name === "AbortError" ? "Timed out after 5 min" : String(e));
    } finally {
      clearTimeout(timeout);
      setJudgeLoading(false);
    }
  }

  if (error) {
    return (
      <main className="min-h-screen flex items-center justify-center">
        <div className="text-center space-y-4">
          <p className="text-sm" style={{ color: "var(--rust)" }}>{error}</p>
          <button
            onClick={() => router.push("/")}
            className="text-sm underline transition-colors"
            style={{ color: "var(--text-3)" }}
          >
            Go back
          </button>
        </div>
      </main>
    );
  }

  const spinnerStyle: React.CSSProperties = {
    borderColor: "var(--sage) transparent var(--sage) var(--sage)",
  };

  return (
    <main className="min-h-screen px-4 py-12 max-w-5xl mx-auto space-y-12">

      {/* Header */}
      <div className="text-center space-y-2">
        <h1 className="text-3xl tracking-tight">
          <span className="font-light" style={{ color: "var(--text-1)" }}>Reading</span>
          <span style={{ fontFamily: "var(--font-dm-serif)", color: "var(--sage)", fontStyle: "italic" }}>DNA</span>
        </h1>
        <div className="flex items-center justify-center gap-3 text-sm" style={{ color: "var(--text-3)" }}>
          <span><span className="font-medium" style={{ color: "var(--text-1)" }}>{books.length}</span> read</span>
          {currentlyReadingCount > 0 && <><span style={{ color: "var(--border-mid)" }}>·</span><span><span className="font-medium" style={{ color: "var(--text-1)" }}>{currentlyReadingCount}</span> reading now</span></>}
          {dnfCount > 0 && <><span style={{ color: "var(--border-mid)" }}>·</span><span><span className="font-medium" style={{ color: "var(--text-1)" }}>{dnfCount}</span> did not finish</span></>}
          {wantToReadCount > 0 && <><span style={{ color: "var(--border-mid)" }}>·</span><span><span className="font-medium" style={{ color: "var(--text-1)" }}>{wantToReadCount}</span> want to read</span></>}
          <span style={{ color: "var(--border-mid)" }}>·</span>
          <span><span className="font-medium" style={{ color: "var(--text-1)" }}>{books.length + currentlyReadingCount + dnfCount + wantToReadCount}</span> total</span>
        </div>
      </div>

      {/* DNA Profile + Share Card */}
      {dna ? (
        <div className="flex flex-col lg:flex-row gap-8 items-start">
          <div className="flex-1 min-w-0">
            <DNAProfile dna={dna} />
          </div>
          <div className="lg:sticky lg:top-8 shrink-0">
            <ShareCard dna={dna} bookCount={books.length} />
          </div>
        </div>
      ) : (
        <div
          className="flex items-center gap-3 rounded-2xl p-8"
          style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
        >
          <div className="w-5 h-5 border-2 border-t-transparent rounded-full animate-spin shrink-0" style={spinnerStyle} />
          <span className="text-sm" style={{ color: "var(--text-2)" }}>Building your Reading DNA…</span>
        </div>
      )}

      {/* Map + Battle */}
      {dna && (
        mapData && battle ? (
          <UnifiedMap
            mapData={mapData}
            battle={{ ...battle, ...(judgeData ?? {}) }}
            libbyData={libbyData}
            library={library}
            judgeLoading={judgeLoading}
            judgeError={judgeError}
            onRunJudge={runJudge}
          />
        ) : (
          <div className="space-y-4">
            <h2
              className="text-xl font-light tracking-tight"
              style={{ fontFamily: "var(--font-dm-serif)", color: "var(--text-1)", fontStyle: "italic" }}
            >
              Reading Universe
            </h2>
            <div
              className="rounded-2xl p-8 flex items-center gap-3"
              style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
            >
              <div className="w-5 h-5 border-2 border-t-transparent rounded-full animate-spin shrink-0" style={spinnerStyle} />
              <span className="text-sm" style={{ color: "var(--text-2)" }}>
                {step === "battle"
                  ? "Running AI model battle — two models are picking books for you…"
                  : "Mapping your reading universe…"}
              </span>
            </div>
          </div>
        )
      )}
    </main>
  );
}
