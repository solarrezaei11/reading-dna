"use client";

import { useState } from "react";

type Props = {
  battle: any;
  libbyData: any;
  library: string;
};

const MODEL_COLORS: Record<string, string> = {
  "GPT-4o": "blue",
  "Gemini 2.0 Flash": "green",
  "Llama 4 (Cerebras)": "orange",
};

const COLOR_MAP: Record<string, { bg: string; border: string; text: string; badge: string }> = {
  blue: {
    bg: "bg-blue-500/5",
    border: "border-blue-500/20",
    text: "text-blue-400",
    badge: "bg-blue-500/20 text-blue-300 border-blue-500/30",
  },
  green: {
    bg: "bg-emerald-500/5",
    border: "border-emerald-500/20",
    text: "text-emerald-400",
    badge: "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
  },
  orange: {
    bg: "bg-orange-500/5",
    border: "border-orange-500/20",
    text: "text-orange-400",
    badge: "bg-orange-500/20 text-orange-300 border-orange-500/30",
  },
};

function LibbyBadge({ isbn, libbyData }: { isbn: string; libbyData: any }) {
  if (!libbyData || !isbn) return null;
  const result = libbyData.results?.[isbn];
  if (!result) return null;
  if (result.status === "available") {
    return <span className="text-xs bg-green-500/20 text-green-400 border border-green-500/30 px-2 py-0.5 rounded-full">Available on Libby</span>;
  }
  if (result.status === "waitlist") {
    return <span className="text-xs bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 px-2 py-0.5 rounded-full">~{result.wait_weeks}w wait on Libby</span>;
  }
  return null;
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  return (
    <div className="space-y-0.5">
      <div className="flex justify-between text-xs">
        <span className="text-zinc-500 capitalize">{label.replace("_", " ")}</span>
        <span className="text-zinc-300">{value}/10</span>
      </div>
      <div className="h-1 bg-white/10 rounded-full overflow-hidden">
        <div className="h-full bg-purple-500 rounded-full" style={{ width: `${value * 10}%` }} />
      </div>
    </div>
  );
}

export default function LLMBattle({ battle, libbyData, library }: Props) {
  const [activeModel, setActiveModel] = useState<string>(battle.winner || Object.keys(battle.models)[0]);
  const models = battle.models as Record<string, any>;

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-zinc-300">LLM Battle</h2>
        <div className="text-xs text-zinc-500">GPT-OSS 120B vs GLM 4.7 — same prompt, different models</div>
      </div>

      {/* Model tabs */}
      <div className="flex gap-2 flex-wrap">
        {Object.keys(models).map((name) => {
          const color = MODEL_COLORS[name] || "blue";
          const c = COLOR_MAP[color];
          const isActive = activeModel === name;
          const score = models[name]?.scores?.total;
          return (
            <button
              key={name}
              onClick={() => setActiveModel(name)}
              className={`px-4 py-2 rounded-xl text-sm font-medium border transition-all ${
                isActive
                  ? `${c.bg} ${c.border} ${c.text}`
                  : "bg-white/5 border-white/10 text-zinc-400 hover:text-white"
              }`}
            >
              {name}
            </button>
          );
        })}
      </div>

      {/* Active model panel */}
      {activeModel && models[activeModel] && (() => {
        const model = models[activeModel];
        const color = MODEL_COLORS[activeModel] || "blue";
        const c = COLOR_MAP[color];
        return (
          <div className={`${c.bg} border ${c.border} rounded-2xl p-6 space-y-6`}>

            {/* Recommendations */}
            <div className="space-y-3">
              <div className="text-xs text-zinc-400 font-mono tracking-widest uppercase">Recommendations</div>
              <div className="space-y-3">
                {(model.recommendations || []).map((rec: any, i: number) => (
                  <div key={i} className="bg-black/20 rounded-xl p-4 space-y-2">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="font-medium text-sm">{rec.title}</div>
                        <div className="text-xs text-zinc-500">{rec.author} {rec.year ? `· ${rec.year}` : ""}</div>
                      </div>
                      <div className="flex gap-1.5 flex-shrink-0 flex-wrap justify-end">
                        {rec.comfort_zone === false && (
                          <span className="text-xs bg-yellow-500/10 text-yellow-400 border border-yellow-500/20 px-2 py-0.5 rounded-full">
                            Outside comfort zone
                          </span>
                        )}
                        <LibbyBadge isbn={rec.isbn} libbyData={libbyData} />
                      </div>
                    </div>
                    <p className="text-xs text-zinc-400 leading-relaxed">{rec.why}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        );
      })()}

      {library && !libbyData && (
        <p className="text-xs text-zinc-500">Checking Libby availability at {library}...</p>
      )}
    </section>
  );
}
