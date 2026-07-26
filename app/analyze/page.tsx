"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import DNAProfile from "@/components/DNAProfile";
import UnifiedMap from "@/components/UnifiedMap";
import SpeedComparison from "@/components/SpeedComparison";
import ShareCard from "@/components/ShareCard";
import FamousReaderMatch from "@/components/FamousReaderMatch";
import PredictBook from "@/components/PredictBook";
import { api, apiErrorMessage, judgeFailureMessage } from "@/lib/api";
import { normalizeRecommendations } from "@/lib/recommendations";
import { readAnalysisInput } from "@/lib/session";
import type { AnalysisInput, BattleResult, DnaProfile, JudgeResponse, LibbyResponse, MapData } from "@/lib/types";

type Phase = "dna" | "battle" | "complete";

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export default function AnalyzePage() {
  const router = useRouter();
  const [input, setInput] = useState<AnalysisInput | null>(null);
  const [phase, setPhase] = useState<Phase>("dna");
  const [dna, setDna] = useState<DnaProfile | null>(null);
  const [battle, setBattle] = useState<BattleResult | null>(null);
  const [mapData, setMapData] = useState<MapData | null>(null);
  const [libbyData, setLibbyData] = useState<LibbyResponse | null>(null);
  const [pipelineError, setPipelineError] = useState("");
  const [mapError, setMapError] = useState("");
  const [libbyError, setLibbyError] = useState("");
  const [judgeData, setJudgeData] = useState<JudgeResponse | null>(null);
  const [judgeLoading, setJudgeLoading] = useState(false);
  const [judgeError, setJudgeError] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const judgeControllerRef = useRef<AbortController | null>(null);
  const mapRetryControllerRef = useRef<AbortController | null>(null);
  const libbyRetryControllerRef = useRef<AbortController | null>(null);
  const runIdRef = useRef(0);

  const runMap = useCallback(async (analysisInput: AnalysisInput, battleResult: BattleResult, signal: AbortSignal) => {
    setMapError("");
    const recommendations = Object.entries(battleResult.models).flatMap(([modelName, model]) =>
      model.recommendations.map((recommendation) => ({ ...recommendation, model_name: modelName })),
    );
    try {
      setMapData(await api.embeddings(analysisInput.books, recommendations, signal));
    } catch (error) {
      if (!isAbort(error)) setMapError(apiErrorMessage(error));
    }
  }, []);

  const runLibby = useCallback(async (analysisInput: AnalysisInput, battleResult: BattleResult, signal: AbortSignal) => {
    if (!analysisInput.library) return;
    setLibbyError("");
    const isbns = [...new Set(
      normalizeRecommendations(battleResult.models)
        .map((recommendation) => recommendation.isbn)
        .filter((isbn): isbn is string => Boolean(isbn)),
    )];
    if (!isbns.length) {
      setLibbyData({
        library_found: false,
        skipped_reason: "no_isbns",
        results: {},
        warnings: ["Availability could not be checked because the recommendations did not include verified ISBNs."],
      });
      return;
    }
    try {
      setLibbyData(await api.libby(isbns, analysisInput.library, signal));
    } catch (error) {
      if (!isAbort(error)) setLibbyError(apiErrorMessage(error));
    }
  }, []);

  const runPipeline = useCallback(async (analysisInput: AnalysisInput) => {
    controllerRef.current?.abort();
    mapRetryControllerRef.current?.abort();
    libbyRetryControllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const runId = ++runIdRef.current;
    setPipelineError("");
    setDna(null);
    setBattle(null);
    setMapData(null);
    setLibbyData(null);
    setMapError("");
    setLibbyError("");
    setJudgeData(null);
    setJudgeError("");
    try {
      setPhase("dna");
      const dnaResult = await api.dna(analysisInput.books, analysisInput.currentlyReading, analysisInput.dnf, controller.signal);
      if (controller.signal.aborted || runId !== runIdRef.current) return;
      setDna(dnaResult);

      setPhase("battle");
      const battleResult = await api.battle(
        dnaResult,
        analysisInput.books,
        analysisInput.currentlyReading,
        analysisInput.dnf,
        analysisInput.wantToRead,
        controller.signal,
      );
      if (controller.signal.aborted || runId !== runIdRef.current) return;
      setBattle(battleResult);
      setPhase("complete");

      void runMap(analysisInput, battleResult, controller.signal);
      void runLibby(analysisInput, battleResult, controller.signal);
    } catch (error) {
      if (!isAbort(error) && runId === runIdRef.current) setPipelineError(apiErrorMessage(error));
    }
  }, [runLibby, runMap]);

  useEffect(() => {
    // The deferred call is cancelled during React's Strict Mode effect replay.
    const timer = window.setTimeout(() => {
      const stored = readAnalysisInput(sessionStorage);
      if (!stored) {
        router.replace("/");
        return;
      }
      setInput(stored);
      void runPipeline(stored);
    }, 0);
    return () => {
      window.clearTimeout(timer);
      controllerRef.current?.abort();
      judgeControllerRef.current?.abort();
      mapRetryControllerRef.current?.abort();
      libbyRetryControllerRef.current?.abort();
    };
  }, [router, runPipeline]);

  const retryMap = useCallback(() => {
    if (!input || !battle) return;
    mapRetryControllerRef.current?.abort();
    const controller = new AbortController();
    mapRetryControllerRef.current = controller;
    void runMap(input, battle, controller.signal);
  }, [battle, input, runMap]);

  const retryLibby = useCallback(() => {
    if (!input || !battle) return;
    libbyRetryControllerRef.current?.abort();
    const controller = new AbortController();
    libbyRetryControllerRef.current = controller;
    void runLibby(input, battle, controller.signal);
  }, [battle, input, runLibby]);

  const runJudge = useCallback(async () => {
    if (!dna || !battle || judgeLoading) return;
    setJudgeLoading(true);
    setJudgeError("");
    judgeControllerRef.current?.abort();
    const controller = new AbortController();
    judgeControllerRef.current = controller;
    try {
      const response = await api.judge(dna, battle, controller.signal);
      const failure = judgeFailureMessage(response);
      if (failure) {
        setJudgeError(failure);
        return;
      }
      setJudgeData(response);
    } catch (error) {
      if (!isAbort(error)) setJudgeError(apiErrorMessage(error));
    } finally {
      setJudgeLoading(false);
    }
  }, [battle, dna, judgeLoading]);

  if (pipelineError) {
    return (
      <main className="min-h-screen flex items-center justify-center px-4">
        <div className="max-w-md text-center space-y-4">
          <p role="alert" className="text-sm" style={{ color: "var(--rust)" }}>{pipelineError}</p>
          <div className="flex justify-center gap-4">
            <button onClick={() => input && void runPipeline(input)} className="text-sm underline" style={{ color: "var(--sage-dark)" }}>Try again</button>
            <button onClick={() => router.push("/")} className="text-sm underline" style={{ color: "var(--text-3)" }}>Choose another import</button>
          </div>
        </div>
      </main>
    );
  }

  const spinnerStyle: React.CSSProperties = { borderColor: "var(--sage) transparent var(--sage) var(--sage)" };
  const warnings = [...new Set(
    [input?.warnings, dna?.warnings, battle?.warnings, mapData?.warnings, libbyData?.warnings]
      .flat()
      .filter((warning): warning is string => typeof warning === "string"),
  )];

  return (
    <main className="min-h-screen px-4 py-12 max-w-5xl mx-auto space-y-12">
      <header className="text-center space-y-2">
        <h1 className="text-3xl tracking-tight">
          <span className="font-light" style={{ color: "var(--text-1)" }}>Reading</span>
          <span style={{ fontFamily: "var(--font-dm-serif)", color: "var(--sage)", fontStyle: "italic" }}>DNA</span>
        </h1>
        {input && (
          <div className="flex flex-wrap items-center justify-center gap-2 text-sm" style={{ color: "var(--text-3)" }}>
            <span><strong style={{ color: "var(--text-1)" }}>{input.books.length}</strong> read</span>
            {input.currentlyReading.length > 0 && <span>· {input.currentlyReading.length} reading now</span>}
            {input.dnf.length > 0 && <span>· {input.dnf.length} did not finish</span>}
            {input.wantToRead.length > 0 && <span>· {input.wantToRead.length} want to read</span>}
          </div>
        )}
      </header>

      {warnings.length > 0 && (
        <div role="status" className="rounded-xl p-3 text-xs" style={{ background: "var(--surface-2)", color: "var(--text-2)", border: "1px solid var(--border)" }}>
          {warnings.join(" ")}
        </div>
      )}

      {dna && input ? (
        <div className="flex flex-col lg:flex-row gap-8 items-start">
          <div className="flex-1 min-w-0 space-y-4">
            <DNAProfile dna={dna} />
            <PredictBook dna={dna} books={input.books} />
            <FamousReaderMatch dna={dna} />
          </div>
          <div className="lg:sticky lg:top-8 shrink-0"><ShareCard dna={dna} bookCount={input.books.length} /></div>
        </div>
      ) : (
        <LoadingCard label="Building your Reading DNA…" spinnerStyle={spinnerStyle} />
      )}

      {battle ? (
        <>
          <SpeedComparison battle={battle} />
          <UnifiedMap
            mapData={mapData}
            mapError={mapError}
            battle={{ ...battle, ...(judgeData ?? {}) }}
            libbyData={libbyData}
            libbyError={libbyError}
            library={input?.library ?? ""}
            judgeLoading={judgeLoading}
            judgeError={judgeError}
            onRunJudge={runJudge}
            onRetryMap={retryMap}
            onRetryLibby={retryLibby}
          />
        </>
      ) : dna ? (
        <LoadingCard
          label={phase === "battle" ? "Running AI model battle — models are selecting books for you…" : "Preparing your recommendations…"}
          spinnerStyle={spinnerStyle}
        />
      ) : null}
    </main>
  );
}

function LoadingCard({ label, spinnerStyle }: { label: string; spinnerStyle: React.CSSProperties }) {
  return (
    <div className="rounded-2xl p-8 flex items-center gap-3" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
      <div className="w-5 h-5 border-2 border-t-transparent rounded-full animate-spin shrink-0 motion-reduce:animate-none" style={spinnerStyle} />
      <span className="text-sm" style={{ color: "var(--text-2)" }}>{label}</span>
    </div>
  );
}
