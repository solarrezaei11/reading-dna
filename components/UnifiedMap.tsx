"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import * as d3 from "d3";

type UserPoint = {
  title: string;
  author: string;
  my_rating: number;
  cluster_id: number;
  cluster_name: string;
  x: number;
  y: number;
};

type GenreAnchor = {
  name: string;
  x: number;
  y: number;
  explored: boolean;
};

type RecPoint = {
  title: string;
  author: string;
  year?: string;
  isbn?: string;
  why?: string;
  comfort_zone?: boolean;
  model_name?: string;
  x: number;
  y: number;
};

type DedupedRec = RecPoint & { isConsensus: boolean };

type BattleModel = {
  recommendations: any[];
  meta: { latency_ms: number; ttft_ms: number | null; generation_ms: number | null; prompt_tokens: number; completion_tokens: number } | null;
  info: { display: string; description: string };
  error?: string;
};

type Props = {
  mapData: {
    points: UserPoint[];
    genre_anchors: GenreAnchor[];
    rec_points: RecPoint[];
  };
  battle: { models: Record<string, BattleModel> };
  libbyData?: Record<string, { available: boolean; title: string; url: string }>;
  library?: string;
};

type TooltipData =
  | { type: "book"; content: UserPoint; x: number; y: number }
  | { type: "rec"; content: DedupedRec; x: number; y: number }
  | { type: "genre"; content: GenreAnchor; x: number; y: number };

const CLUSTER_COLORS = ["#a855f7", "#3b82f6", "#10b981", "#f59e0b", "#ef4444"];

// Keyed by display name returned from battle API
const MODEL_COLORS: Record<string, string> = {
  "GPT-OSS 120B": "#22d3ee",
  "GLM 4.7": "#fb923c",
};
const BOTH_COLOR = "#f59e0b"; // gold for consensus glow rings

export default function UnifiedMap({ mapData, battle, libbyData }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [expandedCluster, setExpandedCluster] = useState<number | null>(null);
  const [activeModel, setActiveModel] = useState<string | null>(null);
  const [selectedGenre, setSelectedGenre] = useState<GenreAnchor | null>(null);
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);

  const { points, genre_anchors, rec_points } = mapData;
  const modelEntries = Object.entries(battle.models);
  const modelNames = modelEntries.map(([n]) => n);

  // Compute per-cluster metadata
  const clusters = useMemo(() => {
    const map = new Map<number, { id: number; books: UserPoint[]; cx: number; cy: number; name: string; color: string }>();
    const ids = [...new Set(points.map(p => p.cluster_id))].sort();
    ids.forEach(id => {
      const books = points.filter(p => p.cluster_id === id).sort((a, b) => b.my_rating - a.my_rating);
      const cx = books.reduce((s, b) => s + b.x, 0) / books.length;
      const cy = books.reduce((s, b) => s + b.y, 0) / books.length;
      map.set(id, { id, books, cx, cy, name: books[0]?.cluster_name ?? `Cluster ${id}`, color: CLUSTER_COLORS[id % CLUSTER_COLORS.length] });
    });
    return map;
  }, [points]);

  // Deduplicate recs: same title from both models → one consensus diamond
  const dedupedRecs = useMemo<DedupedRec[]>(() => {
    const titleModels = new Map<string, string[]>();
    rec_points.forEach(r => {
      const key = r.title.toLowerCase().trim();
      titleModels.set(key, [...(titleModels.get(key) || []), r.model_name || ""]);
    });
    const seen = new Set<string>();
    const result: DedupedRec[] = [];
    rec_points.forEach(r => {
      const key = r.title.toLowerCase().trim();
      const models = titleModels.get(key) || [];
      const isConsensus = new Set(models).size > 1;
      if (isConsensus) {
        if (!seen.has(key)) { seen.add(key); result.push({ ...r, model_name: "both", isConsensus: true }); }
      } else {
        result.push({ ...r, isConsensus: false });
      }
    });
    return result;
  }, [rec_points]);

  useEffect(() => {
    if (!svgRef.current || !points.length) return;
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const W = svgRef.current.clientWidth || 720;
    const H = 500;
    const PAD = 52;
    svg.attr("viewBox", `0 0 ${W} ${H}`);

    const xS = d3.scaleLinear().domain([0, 1]).range([PAD, W - PAD]);
    const yS = d3.scaleLinear().domain([0, 1]).range([H - PAD, PAD]);

    // Filters
    const defs = svg.append("defs");
    const addGlow = (id: string, stdDev: number) => {
      const f = defs.append("filter").attr("id", id).attr("x", "-70%").attr("y", "-70%").attr("width", "240%").attr("height", "240%");
      f.append("feGaussianBlur").attr("stdDeviation", stdDev).attr("result", "blur");
      const fm = f.append("feMerge");
      fm.append("feMergeNode").attr("in", "blur");
      fm.append("feMergeNode").attr("in", "SourceGraphic");
    };
    addGlow("glow-sm", 3);
    addGlow("glow-lg", 6);

    // ── LAYER 0: Genre anchors ─────────────────────────────────────
    genre_anchors.forEach(anchor => {
      const cx = xS(anchor.x);
      const cy = yS(anchor.y);
      const isSelected = selectedGenre?.name === anchor.name;
      const clickable = !anchor.explored;

      // All genre circles are uniform dark grey — cluster bubbles show where your books are
      const anchorFillOpacity = activeModel ? 0.06 : (isSelected ? 0.2 : 0.15);
      const anchorStrokeOpacity = activeModel ? 0.06 : (isSelected ? 0.7 : 0.25);
      const anchorTextFill = activeModel ? "#1f2937" : (isSelected ? "#9ca3af" : "#374151");

      svg.append("circle")
        .attr("cx", cx).attr("cy", cy).attr("r", 50)
        .attr("fill", "#111827")
        .attr("fill-opacity", anchorFillOpacity)
        .attr("stroke", isSelected ? "#6b7280" : "#1f2937")
        .attr("stroke-width", isSelected ? 2 : 1)
        .attr("stroke-opacity", anchorStrokeOpacity)
        .attr("cursor", !activeModel ? "pointer" : "default")
        .on("click", () => {
          if (activeModel) return;
          setSelectedGenre(prev => prev?.name === anchor.name ? null : anchor);
          setExpandedCluster(null);
        })
        .on("mouseenter", (e: MouseEvent) => {
          if (activeModel) return;
          const rect = svgRef.current!.getBoundingClientRect();
          setTooltip({ type: "genre", content: anchor, x: e.clientX - rect.left, y: e.clientY - rect.top });
        })
        .on("mouseleave", () => setTooltip(null));

      svg.append("text")
        .attr("x", cx).attr("y", cy + 4)
        .attr("text-anchor", "middle")
        .attr("fill", anchorTextFill)
        .attr("font-size", 9.5).attr("font-weight", "400")
        .attr("pointer-events", "none")
        .text(anchor.name);
    });

    // ── LAYER 1: Cluster bubbles / expanded dots ───────────────────
    const rScale = d3.scaleLog().domain([1, 5]).range([4, 12]).clamp(true);

    clusters.forEach(cluster => {
      const cx = xS(cluster.cx);
      const cy = yS(cluster.cy);
      const { color, books, id, name } = cluster;
      const radius = Math.min(Math.max(36, Math.sqrt(books.length) * 9), 88);
      const isExpanded = expandedCluster === id;
      const dimBubble = activeModel !== null;

      if (isExpanded) {
        // Dashed outline showing cluster boundary
        svg.append("circle")
          .attr("cx", cx).attr("cy", cy).attr("r", radius)
          .attr("fill", color).attr("fill-opacity", dimBubble ? 0.02 : 0.05)
          .attr("stroke", color).attr("stroke-opacity", 0.3)
          .attr("stroke-width", 1.5).attr("stroke-dasharray", "5 3")
          .attr("cursor", "pointer")
          .on("click", () => setExpandedCluster(null));

        // Label above
        svg.append("text")
          .attr("x", cx).attr("y", cy - radius - 6)
          .attr("text-anchor", "middle")
          .attr("fill", color).attr("font-size", 11).attr("font-weight", "700")
          .attr("opacity", dimBubble ? 0.25 : 0.9)
          .attr("pointer-events", "none")
          .text(name);

        // Individual book dots
        books.forEach(b => {
          svg.append("circle")
            .attr("cx", xS(b.x)).attr("cy", yS(b.y))
            .attr("r", rScale(Math.max(1, b.my_rating)))
            .attr("fill", color)
            .attr("fill-opacity", dimBubble ? 0.15 : 0.85)
            .attr("stroke", "rgba(255,255,255,0.2)").attr("stroke-width", 1)
            .attr("cursor", "pointer")
            .on("mouseenter", (e: MouseEvent) => {
              const rect = svgRef.current!.getBoundingClientRect();
              setTooltip({ type: "book", content: b, x: e.clientX - rect.left, y: e.clientY - rect.top });
              d3.select(e.currentTarget as Element).attr("stroke", "white").attr("stroke-width", 2).attr("fill-opacity", 1);
            })
            .on("mouseleave", (e: MouseEvent) => {
              setTooltip(null);
              d3.select(e.currentTarget as Element).attr("stroke", "rgba(255,255,255,0.2)").attr("stroke-width", 1).attr("fill-opacity", dimBubble ? 0.15 : 0.85);
            });
        });
      } else {
        // Collapsed bubble
        const otherExpanded = expandedCluster !== null && expandedCluster !== id;
        const fillOpacity = dimBubble ? 0.07 : (otherExpanded ? 0.06 : 0.18);
        const strokeOpacity = dimBubble ? 0.15 : (otherExpanded ? 0.15 : 0.55);

        svg.append("circle")
          .attr("cx", cx).attr("cy", cy).attr("r", radius)
          .attr("fill", color).attr("fill-opacity", fillOpacity)
          .attr("stroke", color).attr("stroke-width", 1.5).attr("stroke-opacity", strokeOpacity)
          .attr("cursor", "pointer")
          .on("click", () => { setExpandedCluster(id); setSelectedGenre(null); setTooltip(null); })
          .on("mouseenter", (e: MouseEvent) => {
            d3.select(e.currentTarget as Element).attr("fill-opacity", Math.min(fillOpacity + 0.1, 0.35));
          })
          .on("mouseleave", (e: MouseEvent) => {
            d3.select(e.currentTarget as Element).attr("fill-opacity", fillOpacity);
          });

        svg.append("text")
          .attr("x", cx).attr("y", cy - 7)
          .attr("text-anchor", "middle")
          .attr("fill", color).attr("font-size", 11).attr("font-weight", "700")
          .attr("opacity", dimBubble ? 0.2 : (otherExpanded ? 0.2 : 1))
          .attr("pointer-events", "none")
          .text(name);

        svg.append("text")
          .attr("x", cx).attr("y", cy + 9)
          .attr("text-anchor", "middle")
          .attr("fill", color).attr("font-size", 9)
          .attr("opacity", dimBubble ? 0.15 : (otherExpanded ? 0.15 : 0.65))
          .attr("pointer-events", "none")
          .text(`${books.length} books`);
      }
    });

    // ── LAYER 2: Recommendation diamonds ──────────────────────────
    dedupedRecs.forEach(r => {
      const cx = xS(r.x);
      const cy = yS(r.y);
      const { isConsensus, model_name, comfort_zone } = r;
      const isOutside = comfort_zone === false;
      const s = 8;

      // Opacity based on activeModel filter
      let alpha = 1;
      if (activeModel) {
        if (isConsensus) alpha = 1;
        else if (model_name === activeModel) alpha = 1;
        else alpha = 0.06;
      }

      // Outer glow ring for outside-comfort-zone
      if (isOutside && alpha > 0.1) {
        svg.append("circle")
          .attr("cx", cx).attr("cy", cy).attr("r", 16)
          .attr("fill", "none")
          .attr("stroke", isConsensus ? BOTH_COLOR : (MODEL_COLORS[model_name || ""] || "#fff"))
          .attr("stroke-width", 1.5).attr("stroke-opacity", 0.5 * alpha)
          .attr("filter", "url(#glow-lg)")
          .attr("pointer-events", "none");
      }

      if (isConsensus) {
        // Split diamond: left half = model 0 color, right half = model 1 color
        const c1 = MODEL_COLORS[modelNames[0]] || "#22d3ee";
        const c2 = MODEL_COLORS[modelNames[1]] || "#fb923c";
        svg.append("path")
          .attr("d", `M${cx},${cy - s} L${cx - s},${cy} L${cx},${cy + s}Z`)
          .attr("fill", c1).attr("fill-opacity", alpha).attr("pointer-events", "none");
        svg.append("path")
          .attr("d", `M${cx},${cy - s} L${cx + s},${cy} L${cx},${cy + s}Z`)
          .attr("fill", c2).attr("fill-opacity", alpha).attr("pointer-events", "none");
        svg.append("path")
          .attr("d", `M${cx},${cy - s} L${cx + s},${cy} L${cx},${cy + s} L${cx - s},${cy}Z`)
          .attr("fill", "none").attr("stroke", "rgba(255,255,255,0.25)").attr("stroke-width", 0.5)
          .attr("filter", isOutside ? "url(#glow-sm)" : null)
          .attr("opacity", alpha).attr("pointer-events", "none");
      } else {
        const color = MODEL_COLORS[model_name || ""] || "#ffffff";
        svg.append("path")
          .attr("d", `M${cx},${cy - s} L${cx + s},${cy} L${cx},${cy + s} L${cx - s},${cy}Z`)
          .attr("fill", color).attr("fill-opacity", alpha)
          .attr("stroke", isOutside ? color : "rgba(0,0,0,0.25)").attr("stroke-width", isOutside ? 1 : 0.5)
          .attr("filter", isOutside ? "url(#glow-sm)" : null)
          .attr("pointer-events", "none");
      }

      // Invisible hit-target for tooltip
      svg.append("path")
        .attr("d", `M${cx},${cy - s - 5} L${cx + s + 5},${cy} L${cx},${cy + s + 5} L${cx - s - 5},${cy}Z`)
        .attr("fill", "transparent").attr("cursor", alpha > 0.1 ? "pointer" : "default")
        .on("mouseenter", (e: MouseEvent) => {
          if (alpha <= 0.1) return;
          const rect = svgRef.current!.getBoundingClientRect();
          setTooltip({ type: "rec", content: r, x: e.clientX - rect.left, y: e.clientY - rect.top });
        })
        .on("mouseleave", () => setTooltip(null));
    });

  }, [points, genre_anchors, dedupedRecs, clusters, expandedCluster, activeModel, selectedGenre, modelNames]);

  // Side panel: expanded cluster books or genre recs
  const expandedBooks = expandedCluster !== null ? (clusters.get(expandedCluster)?.books || []) : [];
  const expandedClusterName = expandedCluster !== null ? clusters.get(expandedCluster)?.name : null;

  // Always show the 3 nearest recs to any clicked genre — no distance cutoff
  const genreNearRecs = selectedGenre
    ? dedupedRecs
        .map(r => ({ ...r, dist: Math.hypot(r.x - selectedGenre.x, r.y - selectedGenre.y) }))
        .sort((a, b) => a.dist - b.dist)
        .slice(0, 3)
    : [];

  const showPanel = expandedCluster !== null || selectedGenre !== null;

  return (
    <section className="space-y-5">
      {/* Header */}
      <h2 className="text-lg font-semibold text-zinc-300">Reading Universe</h2>

      {/* Legend */}
      <div className="flex flex-wrap gap-x-5 gap-y-2 text-[11px] text-zinc-500">
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full border border-zinc-700 inline-block" />
          genre territory (click for nearest AI picks)
        </span>
        {modelEntries.map(([name]) => (
          <span key={name} className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 inline-block" style={{ background: MODEL_COLORS[name], clipPath: "polygon(50% 0%,100% 50%,50% 100%,0% 50%)" }} />
            {name} pick
          </span>
        ))}
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 inline-block" style={{ background: MODEL_COLORS[modelNames[0]], clipPath: "polygon(0% 0%,0% 100%,50% 50%)" }} />
          <span className="w-2.5 h-2.5 -ml-2.5 inline-block" style={{ background: MODEL_COLORS[modelNames[1]], clipPath: "polygon(100% 0%,100% 100%,50% 50%)" }} />
          both models agree
        </span>
        <span className="flex items-center gap-1.5">
          <span className="relative inline-block w-3 h-3">
            <span className="absolute inset-0 rounded-full border border-amber-400 opacity-70" />
            <span className="absolute inset-1 inline-block" style={{ background: "#22d3ee", clipPath: "polygon(50% 0%,100% 50%,50% 100%,0% 50%)" }} />
          </span>
          glow ring = outside your comfort zone
        </span>
      </div>

      <p className="text-[11px] text-zinc-600 -mt-1">
        Click a cluster bubble to see its books · Click a model card below to highlight its picks on the map
      </p>

      {/* Canvas + Side panel */}
      <div className="flex gap-4 items-stretch">
        <div
          className="relative flex-1 min-w-0 rounded-2xl overflow-hidden"
          style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.07)" }}
        >
          <svg ref={svgRef} className="w-full" style={{ height: 500 }} />

          {/* Tooltip */}
          {tooltip && (
            <div
              className="absolute z-20 pointer-events-none rounded-xl px-3 py-2.5 text-xs shadow-2xl"
              style={{
                left: Math.min(tooltip.x + 14, 350),
                top: Math.max(tooltip.y - 60, 8),
                background: "rgba(8,8,18,0.96)",
                border: "1px solid rgba(255,255,255,0.13)",
                maxWidth: 220,
                backdropFilter: "blur(12px)",
              }}
            >
              {tooltip.type === "book" && (
                <>
                  <div className="font-semibold text-white leading-snug">{tooltip.content.title}</div>
                  <div className="text-zinc-400 mt-0.5">{tooltip.content.author}</div>
                  {tooltip.content.my_rating > 0 && (
                    <div className="text-yellow-400 mt-1 text-[11px] tracking-wide">
                      {"★".repeat(tooltip.content.my_rating)}
                      <span className="text-zinc-700">{"★".repeat(5 - tooltip.content.my_rating)}</span>
                    </div>
                  )}
                </>
              )}
              {tooltip.type === "rec" && (() => {
                const r = tooltip.content;
                const labelColor = r.isConsensus ? BOTH_COLOR : (MODEL_COLORS[r.model_name || ""] || "#fff");
                const label = r.isConsensus ? "Both models" : r.model_name;
                return (
                  <>
                    <div className="text-[10px] uppercase tracking-widest font-bold mb-1.5" style={{ color: labelColor }}>
                      {label}{r.comfort_zone === false ? " · outside comfort zone" : ""}
                    </div>
                    <div className="font-semibold text-white leading-snug">{r.title}</div>
                    <div className="text-zinc-400 mt-0.5">{r.author}</div>
                    {r.why && <div className="text-zinc-500 mt-1.5 text-[10px] leading-snug">{r.why}</div>}
                  </>
                );
              })()}
              {tooltip.type === "genre" && (
                <>
                  <div className="font-semibold text-zinc-200">{tooltip.content.name}</div>
                  <div className="text-zinc-500 text-[10px] mt-0.5">Click to see nearest AI picks</div>
                </>
              )}
            </div>
          )}
        </div>

        {/* Side panel */}
        {showPanel && (
          <div
            className="w-56 shrink-0 rounded-2xl p-4 overflow-y-auto"
            style={{ maxHeight: 500, background: "#0d0d1c", border: "1px solid rgba(255,255,255,0.1)" }}
          >
            {expandedCluster !== null && (
              <>
                <div className="flex items-center justify-between mb-3">
                  <div className="text-[10px] font-bold uppercase tracking-widest" style={{ color: clusters.get(expandedCluster)?.color || "#a78bfa" }}>
                    {expandedClusterName}
                  </div>
                  <span className="text-[10px] text-zinc-500">{expandedBooks.length} books</span>
                </div>
                <div className="space-y-3">
                  {expandedBooks.map((b, i) => (
                    <div key={i} className="pb-3 border-b last:border-0" style={{ borderColor: "rgba(255,255,255,0.06)" }}>
                      <div className="text-xs font-semibold leading-snug" style={{ color: "#e4e4e7" }}>{b.title}</div>
                      <div className="text-[11px] mt-0.5" style={{ color: "#71717a" }}>{b.author}</div>
                      {b.my_rating > 0 && (
                        <div className="text-[10px] mt-0.5" style={{ color: "#eab308" }}>
                          {"★".repeat(b.my_rating)}<span style={{ color: "#3f3f46" }}>{"★".repeat(5 - b.my_rating)}</span>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}

            {selectedGenre !== null && (
              <>
                <div className="text-[10px] font-bold uppercase tracking-widest mb-1" style={{ color: "#a1a1aa" }}>
                  {selectedGenre.name}
                </div>
                {genreNearRecs.length > 0 ? (
                  <div className="space-y-3">
                    <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "#52525b" }}>Nearest AI picks</div>
                    {genreNearRecs.map((r, i) => {
                      const color = r.isConsensus ? BOTH_COLOR : (MODEL_COLORS[r.model_name || ""] || "#a1a1aa");
                      const label = r.isConsensus ? "Both models" : r.model_name;
                      const libby = libbyData?.[r.isbn || ""];
                      return (
                        <div key={i} className="pb-3 border-b last:border-0" style={{ borderColor: "rgba(255,255,255,0.06)" }}>
                          <div className="text-[10px] font-bold mb-0.5" style={{ color }}>{label}</div>
                          <div className="text-xs font-semibold leading-snug" style={{ color: "#e4e4e7" }}>{r.title}</div>
                          <div className="text-[11px] mt-0.5" style={{ color: "#71717a" }}>{r.author}</div>
                          {r.why && <div className="text-[10px] mt-1 leading-snug" style={{ color: "#52525b" }}>{r.why}</div>}
                          {libby?.available && <div className="text-[10px] mt-1" style={{ color: "#34d399" }}>Available on Libby</div>}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="text-[10px] italic" style={{ color: "#52525b" }}>
                    No AI picks mapped yet — try re-running the analysis.
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* Model cards — click to filter map */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
        {modelEntries.map(([name, m]) => {
          const color = MODEL_COLORS[name] || "#ffffff";
          const isActive = activeModel === name;
          const recCount = dedupedRecs.filter(r => !r.isConsensus && r.model_name === name).length;
          const consensusCount = dedupedRecs.filter(r => r.isConsensus).length;

          return (
            <button
              key={name}
              onClick={() => setActiveModel(isActive ? null : name)}
              className="text-left rounded-xl p-4 transition-all"
              style={{
                background: isActive ? `${color}12` : "rgba(255,255,255,0.03)",
                border: `1px solid ${isActive ? color + "50" : "rgba(255,255,255,0.08)"}`,
                borderLeft: `3px solid ${color}`,
              }}
            >
              {/* Name row */}
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="w-3 h-3 shrink-0" style={{ background: color, clipPath: "polygon(50% 0%,100% 50%,50% 100%,0% 50%)" }} />
                  <span className="font-bold text-sm text-white">{name}</span>
                </div>
                <span
                  className="text-[10px] px-2 py-0.5 rounded-full font-medium"
                  style={{ background: isActive ? `${color}25` : "rgba(255,255,255,0.06)", color: isActive ? color : "#71717a" }}
                >
                  {isActive ? "showing" : "click to highlight"}
                </span>
              </div>

              {/* Description */}
              {m.info?.description && (
                <p className="text-[11px] text-zinc-400 leading-relaxed mb-3">{m.info.description}</p>
              )}

              {/* Stats row */}
              <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-[11px]">
                {m.meta && (
                  <>
                    {m.meta.ttft_ms != null && (
                      <div title="Time to first token">
                        <span className="font-mono font-bold" style={{ color }}>{m.meta.ttft_ms.toLocaleString()}</span>
                        <span className="text-zinc-500 ml-1">ms TTFT</span>
                      </div>
                    )}
                    {m.meta.generation_ms != null && (
                      <div title="Generation time (after first token)">
                        <span className="font-mono font-bold text-zinc-300">{m.meta.generation_ms.toLocaleString()}</span>
                        <span className="text-zinc-500 ml-1">ms gen</span>
                      </div>
                    )}
                    <div title="Total latency">
                      <span className="font-mono font-bold text-zinc-500">{m.meta.latency_ms.toLocaleString()}</span>
                      <span className="text-zinc-500 ml-1">ms total</span>
                    </div>
                    {m.meta.prompt_tokens != null && (
                      <div>
                        <span className="font-mono font-bold text-zinc-300">{m.meta.prompt_tokens.toLocaleString()}</span>
                        <span className="text-zinc-500 ml-1">prompt</span>
                      </div>
                    )}
                    {m.meta.completion_tokens != null && (
                      <div>
                        <span className="font-mono font-bold text-zinc-300">{m.meta.completion_tokens.toLocaleString()}</span>
                        <span className="text-zinc-500 ml-1">output</span>
                      </div>
                    )}
                  </>
                )}
                <div>
                  <span className="font-mono font-bold text-zinc-300">{recCount}</span>
                  <span className="text-zinc-500 ml-1">unique picks</span>
                </div>
                {consensusCount > 0 && (
                  <div>
                    <span className="font-mono font-bold" style={{ color: BOTH_COLOR }}>{consensusCount}</span>
                    <span className="text-zinc-500 ml-1">both agreed</span>
                  </div>
                )}
              </div>

              {m.error && <div className="text-[10px] text-red-400 mt-2">Error: {m.error}</div>}
            </button>
          );
        })}
      </div>
    </section>
  );
}
