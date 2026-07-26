"use client";

import { useMemo } from "react";
import type { BattleModel, BattleResult } from "@/lib/types";

type Props = {
  battle: BattleResult;
};

const COLORS = ["#4a8c7e", "#9b7040", "#7a6a8c", "#b06040", "#5a8a5a"];

function colorForModel(name: string, index = 0): string {
  if (name.includes("GPT")) return "#4a8c7e";
  if (name.includes("GLM")) return "#9b7040";
  if (name.includes("Groq")) return "#7a6a8c";
  if (name.includes("OpenRouter")) return "#b06040";
  if (name.includes("Gemma")) return "#5a8a5a";
  return COLORS[index % COLORS.length];
}

type Row = {
  key: string;
  name: string;
  color: string;
  family?: string;
  familyDisplay?: string;
  providerDisplay?: string;
  latencyMs: number | null;
  ttftMs: number | null;
  completionTokens: number | null;
  tokensPerSecond: number | null;
  error?: string;
};

// tok/s over the full request window (completion_tokens / (latency_ms/1000)).
// Dividing by generation_ms alone inflates reasoning models absurdly because
// their reasoning time is recorded inside TTFT while completion_tokens still
// counts the reasoning tokens.
function tokensPerSecond(model: BattleModel): number | null {
  const tokens = model.meta?.completion_tokens;
  const latency = model.meta?.latency_ms;
  if (!tokens || !latency) return null;
  return tokens / (latency / 1000);
}

function toRows(battle: BattleResult): Row[] {
  return Object.entries(battle.models).map(([name, model], index) => ({
    key: name,
    name,
    color: colorForModel(name, index),
    family: model.info?.family,
    familyDisplay: model.info?.family_display,
    providerDisplay: model.info?.provider_display,
    latencyMs: model.meta?.latency_ms ?? null,
    ttftMs: model.meta?.ttft_ms ?? null,
    completionTokens: model.meta?.completion_tokens ?? null,
    tokensPerSecond: tokensPerSecond(model),
    error: model.error,
  }));
}

function fmt(n: number | null | undefined): string {
  return n == null ? "—" : Math.round(n).toLocaleString();
}

export default function SpeedComparison({ battle }: Props) {
  const rows = useMemo(() => toRows(battle), [battle]);

  const ranked = useMemo(
    () =>
      rows
        .filter((r) => !r.error && r.tokensPerSecond != null)
        .sort((a, b) => (b.tokensPerSecond ?? 0) - (a.tokensPerSecond ?? 0)),
    [rows]
  );

  const fastestTps = ranked[0]?.tokensPerSecond ?? 0;

  const families = useMemo(() => {
    const byFamily = new Map<string, Row[]>();
    for (const row of rows) {
      if (!row.family) continue;
      const list = byFamily.get(row.family) ?? [];
      list.push(row);
      byFamily.set(row.family, list);
    }
    // Only families that actually run on 2+ providers are "equivalent" pairs.
    return [...byFamily.entries()]
      .filter(([, list]) => list.length >= 2)
      .map(([family, list]) => ({
        family,
        familyDisplay: list.find((r) => r.familyDisplay)?.familyDisplay ?? family,
        entries: [...list].sort((a, b) => (b.tokensPerSecond ?? -1) - (a.tokensPerSecond ?? -1)),
      }));
  }, [rows]);

  if (rows.length === 0) return null;

  return (
    <section className="space-y-6">
      <div className="space-y-1">
        <h2 className="text-base font-medium" style={{ color: "var(--text-1)" }}>Inference speed comparison</h2>
        <p className="text-xs" style={{ color: "var(--text-3)" }}>
          Throughput is output tokens over the full request window (includes any hidden reasoning tokens).
          Reasoning models show higher time-to-first-token because reasoning happens before the first visible token.
        </p>
      </div>

      {/* Ranked leaderboard */}
      <div className="rounded-2xl p-5 space-y-3" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
        <h3 className="text-sm font-medium" style={{ color: "var(--text-2)" }}>Throughput leaderboard</h3>
        <div className="space-y-2">
          {ranked.length === 0 ? (
            <p className="text-xs" style={{ color: "var(--text-3)" }}>No models returned timing data.</p>
          ) : (
            ranked.map((row, index) => {
              const pct = fastestTps > 0 ? Math.max(6, ((row.tokensPerSecond ?? 0) / fastestTps) * 100) : 0;
              return (
                <div key={row.key} className="space-y-1">
                  <div className="flex items-baseline justify-between gap-3 text-xs">
                    <span className="flex items-center gap-2" style={{ color: "var(--text-1)" }}>
                      <span className="tabular-nums" style={{ color: "var(--text-3)" }}>{index + 1}.</span>
                      <span className="inline-block h-2 w-2 rounded-full" style={{ background: row.color }} />
                      {row.name}
                    </span>
                    <span className="tabular-nums" style={{ color: "var(--text-2)" }}>
                      {fmt(row.tokensPerSecond)} tok/s · {fmt(row.latencyMs)} ms · TTFT {fmt(row.ttftMs)} ms
                    </span>
                  </div>
                  <div className="h-2 w-full rounded-full" style={{ background: "var(--border)" }}>
                    <div className="h-2 rounded-full" style={{ width: `${pct}%`, background: row.color }} />
                  </div>
                </div>
              );
            })
          )}
        </div>
        {rows.some((r) => r.error) && (
          <div className="pt-1 text-[11px] space-y-0.5" style={{ color: "var(--text-3)" }}>
            {rows.filter((r) => r.error).map((r) => (
              <p key={r.key}>{r.name}: {r.error}</p>
            ))}
          </div>
        )}
      </div>

      {/* Equivalent-model cross-provider comparison */}
      {families.length > 0 && (
        <div className="space-y-3">
          <div className="space-y-1">
            <h3 className="text-sm font-medium" style={{ color: "var(--text-2)" }}>Same model, different providers</h3>
            <p className="text-xs" style={{ color: "var(--text-3)" }}>
              The identical model weights run on multiple inference stacks — this isolates the provider’s serving speed.
            </p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {families.map((group) => {
              const winner = group.entries.find((e) => e.tokensPerSecond != null);
              return (
                <article key={group.family} className="rounded-2xl p-5 space-y-3" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
                  <h4 className="text-sm font-medium" style={{ color: "var(--text-1)" }}>{group.familyDisplay}</h4>
                  <div className="space-y-2">
                    {group.entries.map((entry) => {
                      const isWinner = winner && entry.key === winner.key && entry.tokensPerSecond != null;
                      return (
                        <div key={entry.key} className="flex items-baseline justify-between gap-3 text-xs">
                          <span className="flex items-center gap-2" style={{ color: "var(--text-1)" }}>
                            <span className="inline-block h-2 w-2 rounded-full" style={{ background: entry.color }} />
                            {entry.providerDisplay ?? entry.name}
                            {isWinner && (
                              <span className="rounded-full px-2 py-0.5 text-[10px]" style={{ color: "var(--sage-dark)", background: "rgba(90,138,90,0.12)" }}>fastest</span>
                            )}
                          </span>
                          <span className="tabular-nums" style={{ color: "var(--text-2)" }}>
                            {entry.error ? <span style={{ color: "var(--rust)" }}>failed</span> : `${fmt(entry.tokensPerSecond)} tok/s · ${fmt(entry.latencyMs)} ms`}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </article>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
