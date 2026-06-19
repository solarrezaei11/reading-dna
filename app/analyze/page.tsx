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
  const [library, setLibrary] = useState("");
  const [step, setStep] = useState<Step>("dna");
  const [dna, setDna] = useState<any>(null);
  const [battle, setBattle] = useState<any>(null);
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
    setBooks(parsed);
    setCurrentlyReadingCount(currentlyReading.length);
    setDnfCount(dnf.length);
    setLibrary(lib);
    runPipeline(parsed, lib, currentlyReading, dnf);
  }, []);

  async function runPipeline(books: any[], lib: string, currentlyReading: any[] = [], dnf: any[] = []) {
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
        body: JSON.stringify({ dna_profile: dnaData, books, currently_reading: currentlyReading, dnf }),
      });
      if (!battleRes.ok) throw new Error("LLM battle failed");
      const battleData = await battleRes.json();
      setBattle(battleData);

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

  if (error) {
    return (
      <main className="min-h-screen flex items-center justify-center">
        <div className="text-center space-y-4">
          <p className="text-red-400">{error}</p>
          <button onClick={() => router.push("/")} className="text-sm text-zinc-400 hover:text-white underline">
            Go back
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen px-4 py-12 max-w-5xl mx-auto space-y-12">

      {/* Header */}
      <div className="text-center space-y-2">
        <h1 className="text-3xl font-bold">
          Reading<span className="text-purple-400">DNA</span>
        </h1>
        <div className="flex items-center justify-center gap-3 text-sm text-zinc-500">
          <span><span className="text-zinc-300 font-medium">{books.length}</span> read</span>
          {currentlyReadingCount > 0 && <><span className="text-zinc-700">·</span><span><span className="text-zinc-300 font-medium">{currentlyReadingCount}</span> reading now</span></>}
          {dnfCount > 0 && <><span className="text-zinc-700">·</span><span><span className="text-zinc-300 font-medium">{dnfCount}</span> did not finish</span></>}
          <span className="text-zinc-700">·</span>
          <span><span className="text-zinc-300 font-medium">{books.length + currentlyReadingCount + dnfCount}</span> total</span>
        </div>
      </div>

      {/* DNA Profile + Share Card — inline loading */}
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
        <div className="flex items-center gap-3 bg-white/5 border border-white/10 rounded-2xl p-8">
          <div className="w-5 h-5 border-2 border-purple-400 border-t-transparent rounded-full animate-spin shrink-0" />
          <span className="text-zinc-400 text-sm">Building your Reading DNA...</span>
        </div>
      )}

      {/* Unified Map + Battle — inline loading */}
      {dna && (
        mapData && battle ? (
          <UnifiedMap mapData={mapData} battle={battle} libbyData={libbyData} library={library} />
        ) : (
          <div className="space-y-4">
            <h2 className="text-lg font-semibold text-zinc-300">Reading Universe</h2>
            <div
              className="rounded-2xl p-8 flex items-center gap-3"
              style={{ background: "rgba(255,255,255,0.025)", border: "1px solid rgba(255,255,255,0.08)" }}
            >
              <div className="w-5 h-5 border-2 border-purple-400 border-t-transparent rounded-full animate-spin shrink-0" />
              <span className="text-zinc-400 text-sm">
                {step === "battle"
                  ? "Running LLM battle — two models are picking books for you..."
                  : "Mapping your reading universe..."}
              </span>
            </div>
          </div>
        )
      )}
    </main>
  );
}
