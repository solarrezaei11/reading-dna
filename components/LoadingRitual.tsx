"use client";

import { useEffect, useState } from "react";
import HimalayanCat from "./HimalayanCat";

const LINES = [
  "Consulting the shelf…",
  "Reading between your lines…",
  "Brewing your archetype…",
  "Weighing prose against pacing…",
  "Asking two models to disagree…",
  "Mapping your reading universe…",
  "Finding the books you haven't met yet…",
];

// Pipeline stages, in order — the active one glows, completed ones get a leaf
const STAGES = ["dna", "battle", "map", "done"] as const;
const STAGE_LABELS: Record<string, string> = {
  dna: "Reading DNA",
  battle: "Model battle",
  map: "Reading universe",
  done: "Done",
};

export default function LoadingRitual({ step }: { step: string }) {
  const [lineIdx, setLineIdx] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setLineIdx((i) => (i + 1) % LINES.length), 2600);
    return () => clearInterval(id);
  }, []);

  const activeIdx = Math.max(0, STAGES.indexOf(step as (typeof STAGES)[number]));

  return (
    <div
      className="rounded-2xl p-8 flex flex-col items-center gap-5 text-center"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
    >
      <div style={{ animation: "vine-sway 4s ease-in-out infinite" }}>
        <HimalayanCat />
      </div>

      {/* Rotating literary one-liner */}
      <div className="h-5 relative w-full">
        <p
          key={lineIdx}
          className="text-sm absolute inset-0"
          style={{ color: "var(--text-2)", fontFamily: "var(--font-dm-serif)", fontStyle: "italic", animation: "fade-cycle 2.6s ease-in-out" }}
        >
          {LINES[lineIdx]}
        </p>
      </div>

      {/* Vine of stages — a leaf sprouts as each completes */}
      <div className="flex items-center gap-2">
        {STAGES.slice(0, 3).map((s, i) => {
          const done = i < activeIdx;
          const active = i === activeIdx;
          return (
            <div key={s} className="flex items-center gap-2">
              <div className="flex flex-col items-center gap-1.5">
                <div className="relative w-5 h-5 flex items-center justify-center">
                  {done ? (
                    <svg width="18" height="18" viewBox="0 0 18 18" style={{ animation: "leaf-in 0.5s ease-out" }}>
                      <path d="M9 2 C 3 5, 3 13, 9 16 C 15 13, 15 5, 9 2 Z" fill="var(--sage)" opacity="0.85" />
                      <path d="M9 3 L 9 15" stroke="#fff" strokeWidth="0.8" opacity="0.5" />
                    </svg>
                  ) : active ? (
                    <div
                      className="w-3.5 h-3.5 rounded-full border-2 border-t-transparent animate-spin"
                      style={{ borderColor: "var(--sage) transparent var(--sage) var(--sage)" }}
                    />
                  ) : (
                    <div className="w-2 h-2 rounded-full" style={{ background: "var(--border-mid)" }} />
                  )}
                </div>
                <span
                  className="text-[9px] tracking-wider uppercase"
                  style={{ color: active ? "var(--sage-dark)" : "var(--text-3)", fontFamily: "var(--font-geist-mono)" }}
                >
                  {STAGE_LABELS[s]}
                </span>
              </div>
              {i < 2 && (
                <div
                  className="h-px w-8 mb-4"
                  style={{ background: i < activeIdx ? "var(--sage)" : "var(--border-mid)", transition: "background 0.5s" }}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
