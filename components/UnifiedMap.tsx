"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import * as d3 from "d3";

function coverUrl(isbn?: string) {
  if (!isbn) return null;
  return `https://covers.openlibrary.org/b/isbn/${isbn}-M.jpg`;
}

function BookCover({ isbn, title, size = 56 }: { isbn?: string; title: string; size?: number }) {
  const src = coverUrl(isbn);
  const [failed, setFailed] = useState(false);
  if (!src || failed) return null;
  return (
    <img
      src={src}
      alt={title}
      width={size}
      height={size * 1.5}
      onError={() => setFailed(true)}
      className="rounded object-cover shrink-0"
      style={{ width: size, height: size * 1.5, boxShadow: "0 2px 8px rgba(0,0,0,0.12)" }}
    />
  );
}

type UserPoint   = { title: string; author: string; my_rating: number; cluster_id: number; cluster_name: string; x: number; y: number };
type GenreAnchor = { name: string; x: number; y: number; explored: boolean };
type RecPoint    = { title: string; author: string; year?: string; isbn?: string; why?: string; comfort_zone?: boolean; on_tbr?: boolean; hidden_gem?: boolean; model_name?: string; x: number; y: number };
type DedupedRec  = RecPoint & { isConsensus: boolean };
type BattleModel = { recommendations: any[]; meta: { latency_ms: number; ttft_ms: number | null; generation_ms: number | null; prompt_tokens: number; completion_tokens: number } | null; info: { display: string; description: string }; error?: string };

type Props = {
  mapData: { points: UserPoint[]; genre_anchors: GenreAnchor[]; rec_points: RecPoint[] };
  battle: { models: Record<string, BattleModel>; winner?: string | null; judge?: Record<string, { scores?: Record<string, number>; verdict?: string; latency_ms?: number; model?: string; error?: string }> };
  judgeLoading?: boolean;
  judgeError?: string;
  onRunJudge?: () => void;
  libbyData?: Record<string, { available: boolean; title: string; url: string }>;
  library?: string;
};

type TooltipData =
  | { type: "book";  content: UserPoint;   x: number; y: number }
  | { type: "rec";   content: DedupedRec;  x: number; y: number }
  | { type: "genre"; content: GenreAnchor; x: number; y: number };

// Botanical cluster palette — works on light background
const CLUSTER_COLORS = ["#5a8a5a", "#4a8c7e", "#9b7040", "#7a6a8c", "#b06040"];

const MODEL_COLORS: Record<string, string> = {
  "GPT-OSS 120B": "#4a8c7e",  // dusty teal
  "GLM 4.7":      "#9b7040",  // warm amber-brown
};
const BOTH_COLOR = "#5a8a5a"; // sage

export default function UnifiedMap({ mapData, battle, libbyData, judgeLoading, judgeError, onRunJudge }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [expandedCluster, setExpandedCluster]   = useState<number | null>(null);
  const [activeModel,     setActiveModel]        = useState<string | null>(null);
  const [selectedGenre,   setSelectedGenre]      = useState<GenreAnchor | null>(null);
  const [tooltip,         setTooltip]            = useState<TooltipData | null>(null);

  const { points, genre_anchors, rec_points } = mapData;
  const modelEntries = Object.entries(battle.models);
  const modelNames   = modelEntries.map(([n]) => n);

  const clusters = useMemo(() => {
    const map = new Map<number, { id: number; books: UserPoint[]; cx: number; cy: number; name: string; color: string }>();
    [...new Set(points.map(p => p.cluster_id))].sort().forEach(id => {
      const books = points.filter(p => p.cluster_id === id).sort((a, b) => b.my_rating - a.my_rating);
      const cx = books.reduce((s, b) => s + b.x, 0) / books.length;
      const cy = books.reduce((s, b) => s + b.y, 0) / books.length;
      map.set(id, { id, books, cx, cy, name: books[0]?.cluster_name ?? `Cluster ${id}`, color: CLUSTER_COLORS[id % CLUSTER_COLORS.length] });
    });
    return map;
  }, [points]);

  const dedupedRecs = useMemo<DedupedRec[]>(() => {
    const tm = new Map<string, string[]>();
    rec_points.forEach(r => { const k = r.title.toLowerCase().trim(); tm.set(k, [...(tm.get(k) || []), r.model_name || ""]); });
    const seen = new Set<string>(); const result: DedupedRec[] = [];
    rec_points.forEach(r => {
      const k = r.title.toLowerCase().trim(); const models = tm.get(k) || []; const isConsensus = new Set(models).size > 1;
      if (isConsensus) { if (!seen.has(k)) { seen.add(k); result.push({ ...r, model_name: "both", isConsensus: true }); } }
      else result.push({ ...r, isConsensus: false });
    });
    return result;
  }, [rec_points]);

  useEffect(() => {
    if (!svgRef.current || !points.length) return;
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    const W = svgRef.current.clientWidth || 720; const H = 500; const PAD = 52;
    svg.attr("viewBox", `0 0 ${W} ${H}`);
    const xS = d3.scaleLinear().domain([0, 1]).range([PAD, W - PAD]);
    const yS = d3.scaleLinear().domain([0, 1]).range([H - PAD, PAD]);

    const defs = svg.append("defs");
    const addGlow = (id: string, sd: number) => {
      const f = defs.append("filter").attr("id", id).attr("x", "-70%").attr("y", "-70%").attr("width", "240%").attr("height", "240%");
      f.append("feGaussianBlur").attr("stdDeviation", sd).attr("result", "blur");
      const fm = f.append("feMerge"); fm.append("feMergeNode").attr("in", "blur"); fm.append("feMergeNode").attr("in", "SourceGraphic");
    };
    addGlow("glow-sm", 3); addGlow("glow-lg", 6);

    // ── Genre anchors ──
    genre_anchors.forEach(anchor => {
      const cx = xS(anchor.x); const cy = yS(anchor.y);
      const isSelected = selectedGenre?.name === anchor.name;
      const dim = !!activeModel;
      svg.append("circle").attr("cx", cx).attr("cy", cy).attr("r", 50)
        .attr("fill", isSelected ? "rgba(90,138,90,0.10)" : "rgba(139,107,70,0.04)")
        .attr("stroke", isSelected ? "rgba(90,138,90,0.45)" : "rgba(139,107,70,0.18)")
        .attr("stroke-width", isSelected ? 1.5 : 1)
        .attr("stroke-dasharray", isSelected ? "none" : "4 3")
        .attr("opacity", dim ? 0.3 : 1)
        .attr("cursor", !dim ? "pointer" : "default")
        .on("click", () => { if (activeModel) return; setSelectedGenre(prev => prev?.name === anchor.name ? null : anchor); setExpandedCluster(null); })
        .on("mouseenter", (e: MouseEvent) => { if (activeModel) return; const rect = svgRef.current!.getBoundingClientRect(); setTooltip({ type: "genre", content: anchor, x: e.clientX - rect.left, y: e.clientY - rect.top }); })
        .on("mouseleave", () => setTooltip(null));
      svg.append("text").attr("x", cx).attr("y", cy + 4).attr("text-anchor", "middle")
        .attr("fill", dim ? "rgba(160,136,112,0.3)" : (isSelected ? "#5a8a5a" : "#a08870"))
        .attr("font-size", 9.5).attr("font-weight", "400").attr("pointer-events", "none").text(anchor.name);
    });

    // ── Clusters ──
    const rScale = d3.scaleLog().domain([1, 5]).range([4, 12]).clamp(true);
    clusters.forEach(cluster => {
      const cx = xS(cluster.cx); const cy = yS(cluster.cy);
      const { color, books, id, name } = cluster;
      const radius = Math.min(Math.max(36, Math.sqrt(books.length) * 9), 88);
      const isExpanded = expandedCluster === id; const dimBubble = activeModel !== null;

      if (isExpanded) {
        svg.append("circle").attr("cx", cx).attr("cy", cy).attr("r", radius)
          .attr("fill", color).attr("fill-opacity", dimBubble ? 0.03 : 0.08)
          .attr("stroke", color).attr("stroke-opacity", 0.35).attr("stroke-width", 1.5).attr("stroke-dasharray", "5 3")
          .attr("cursor", "pointer").on("click", () => setExpandedCluster(null));
        svg.append("text").attr("x", cx).attr("y", cy - radius - 6).attr("text-anchor", "middle")
          .attr("fill", color).attr("font-size", 11).attr("font-weight", "600").attr("opacity", dimBubble ? 0.2 : 0.9)
          .attr("pointer-events", "none").text(name);
        books.forEach(b => {
          svg.append("circle").attr("cx", xS(b.x)).attr("cy", yS(b.y)).attr("r", rScale(Math.max(1, b.my_rating)))
            .attr("fill", color).attr("fill-opacity", dimBubble ? 0.2 : 0.75)
            .attr("stroke", "rgba(255,255,255,0.6)").attr("stroke-width", 1).attr("cursor", "pointer")
            .on("mouseenter", (e: MouseEvent) => {
              const rect = svgRef.current!.getBoundingClientRect();
              setTooltip({ type: "book", content: b, x: e.clientX - rect.left, y: e.clientY - rect.top });
              d3.select(e.currentTarget as Element).attr("stroke", "#2d2016").attr("stroke-width", 1.5).attr("fill-opacity", 1);
            })
            .on("mouseleave", (e: MouseEvent) => { setTooltip(null); d3.select(e.currentTarget as Element).attr("stroke", "rgba(255,255,255,0.6)").attr("stroke-width", 1).attr("fill-opacity", dimBubble ? 0.2 : 0.75); });
        });
      } else {
        const otherExpanded = expandedCluster !== null && expandedCluster !== id;
        const fo = dimBubble ? 0.06 : (otherExpanded ? 0.06 : 0.18);
        const so = dimBubble ? 0.15 : (otherExpanded ? 0.15 : 0.55);
        svg.append("circle").attr("cx", cx).attr("cy", cy).attr("r", radius)
          .attr("fill", color).attr("fill-opacity", fo).attr("stroke", color).attr("stroke-width", 1.5).attr("stroke-opacity", so)
          .attr("cursor", "pointer")
          .on("click", () => { setExpandedCluster(id); setSelectedGenre(null); setTooltip(null); })
          .on("mouseenter", (e: MouseEvent) => { d3.select(e.currentTarget as Element).attr("fill-opacity", Math.min(fo + 0.1, 0.32)); })
          .on("mouseleave", (e: MouseEvent) => { d3.select(e.currentTarget as Element).attr("fill-opacity", fo); });
        svg.append("text").attr("x", cx).attr("y", cy - 7).attr("text-anchor", "middle")
          .attr("fill", color).attr("font-size", 11).attr("font-weight", "600")
          .attr("opacity", dimBubble ? 0.18 : (otherExpanded ? 0.18 : 1)).attr("pointer-events", "none").text(name);
        svg.append("text").attr("x", cx).attr("y", cy + 9).attr("text-anchor", "middle")
          .attr("fill", color).attr("font-size", 9)
          .attr("opacity", dimBubble ? 0.12 : (otherExpanded ? 0.12 : 0.65)).attr("pointer-events", "none").text(`${books.length} books`);
      }
    });

    // ── Recommendation diamonds ──
    dedupedRecs.forEach(r => {
      const cx = xS(r.x); const cy = yS(r.y); const { isConsensus, model_name, comfort_zone, on_tbr, hidden_gem } = r; const s = 8;
      let alpha = 1;
      if (activeModel) { if (isConsensus) alpha = 1; else if (model_name === activeModel) alpha = 1; else alpha = 0.07; }
      if (comfort_zone === false && alpha > 0.1) {
        svg.append("circle").attr("cx", cx).attr("cy", cy).attr("r", 16)
          .attr("fill", "none").attr("stroke", isConsensus ? BOTH_COLOR : (MODEL_COLORS[model_name || ""] || "#9b7040"))
          .attr("stroke-width", 1.5).attr("stroke-opacity", 0.5 * alpha).attr("filter", "url(#glow-lg)").attr("pointer-events", "none");
      }
      if (isConsensus) {
        const c1 = MODEL_COLORS[modelNames[0]] || "#4a8c7e"; const c2 = MODEL_COLORS[modelNames[1]] || "#9b7040";
        svg.append("path").attr("d", `M${cx},${cy-s} L${cx-s},${cy} L${cx},${cy+s}Z`).attr("fill", c1).attr("fill-opacity", alpha).attr("pointer-events", "none");
        svg.append("path").attr("d", `M${cx},${cy-s} L${cx+s},${cy} L${cx},${cy+s}Z`).attr("fill", c2).attr("fill-opacity", alpha).attr("pointer-events", "none");
        svg.append("path").attr("d", `M${cx},${cy-s} L${cx+s},${cy} L${cx},${cy+s} L${cx-s},${cy}Z`).attr("fill", "none").attr("stroke", "rgba(0,0,0,0.12)").attr("stroke-width", 0.5).attr("opacity", alpha).attr("pointer-events", "none");
      } else {
        const color = MODEL_COLORS[model_name || ""] || "#9b7040";
        svg.append("path").attr("d", `M${cx},${cy-s} L${cx+s},${cy} L${cx},${cy+s} L${cx-s},${cy}Z`)
          .attr("fill", color).attr("fill-opacity", alpha)
          .attr("stroke", comfort_zone === false ? color : "rgba(0,0,0,0.1)").attr("stroke-width", comfort_zone === false ? 1 : 0.5)
          .attr("filter", comfort_zone === false ? "url(#glow-sm)" : null).attr("pointer-events", "none");
      }
      // Dashed sage ring = already on TBR
      if (on_tbr && alpha > 0.1) {
        svg.append("circle").attr("cx", cx).attr("cy", cy).attr("r", s + 5)
          .attr("fill", "none").attr("stroke", "#5a8a5a").attr("stroke-width", 1.5)
          .attr("stroke-opacity", 0.6 * alpha).attr("stroke-dasharray", "3,2").attr("pointer-events", "none");
      }
      // Small star spark = hidden gem (likely no library hold)
      if (hidden_gem && alpha > 0.1) {
        const sparkColor = "#c4a050";
        const sr = 4;
        [[-sr, -sr], [sr, -sr], [sr, sr], [-sr, sr]].forEach(([dx, dy]) => {
          svg.append("line")
            .attr("x1", cx + dx * 0.4).attr("y1", cy + dy * 0.4)
            .attr("x2", cx + dx).attr("y2", cy + dy)
            .attr("stroke", sparkColor).attr("stroke-width", 1.2)
            .attr("stroke-opacity", 0.85 * alpha).attr("pointer-events", "none");
        });
      }
      svg.append("path").attr("d", `M${cx},${cy-s-5} L${cx+s+5},${cy} L${cx},${cy+s+5} L${cx-s-5},${cy}Z`)
        .attr("fill", "transparent").attr("cursor", alpha > 0.1 ? "pointer" : "default")
        .on("mouseenter", (e: MouseEvent) => { if (alpha <= 0.1) return; const rect = svgRef.current!.getBoundingClientRect(); setTooltip({ type: "rec", content: r, x: e.clientX - rect.left, y: e.clientY - rect.top }); })
        .on("mouseleave", () => setTooltip(null));
    });
  }, [points, genre_anchors, dedupedRecs, clusters, expandedCluster, activeModel, selectedGenre, modelNames]);

  const expandedBooks        = expandedCluster !== null ? (clusters.get(expandedCluster)?.books || []) : [];
  const expandedClusterName  = expandedCluster !== null ? clusters.get(expandedCluster)?.name : null;
  const genreNearRecs        = selectedGenre
    ? dedupedRecs.map(r => ({ ...r, dist: Math.hypot(r.x - selectedGenre.x, r.y - selectedGenre.y) })).sort((a, b) => a.dist - b.dist).slice(0, 3)
    : [];
  const showPanel = expandedCluster !== null || selectedGenre !== null;

  const card = (children: React.ReactNode, extra?: React.CSSProperties) => (
    <div style={{ background: "var(--surface)", border: "1px solid var(--border-mid)", boxShadow: "0 1px 6px rgba(139,107,70,0.06)", ...extra }}
      className="rounded-2xl">
      {children}
    </div>
  );

  return (
    <section className="space-y-5">
      <h2 className="text-xl font-light tracking-tight"
        style={{ fontFamily: "var(--font-dm-serif)", color: "var(--text-1)", fontStyle: "italic" }}>
        Reading Universe
      </h2>

      {/* Legend */}
      <div className="flex flex-wrap gap-x-5 gap-y-2 text-[11px]" style={{ color: "var(--text-3)" }}>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full inline-block" style={{ border: "1.5px dashed rgba(139,107,70,0.35)" }} />
          genre territory
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
          both agree
        </span>
        <span className="flex items-center gap-1.5">
          <span className="relative inline-block w-3 h-3">
            <span className="absolute inset-0 rounded-full border opacity-70" style={{ borderColor: "var(--sage)" }} />
            <span className="absolute inset-1 inline-block" style={{ background: MODEL_COLORS[modelNames[0]], clipPath: "polygon(50% 0%,100% 50%,50% 100%,0% 50%)" }} />
          </span>
          outside comfort zone
        </span>
        <span className="flex items-center gap-1.5">
          <span className="relative inline-block w-3 h-3">
            <span className="absolute inset-0 rounded-full" style={{ border: "1.5px dashed #5a8a5a" }} />
            <span className="absolute inset-1 inline-block" style={{ background: MODEL_COLORS[modelNames[0]], clipPath: "polygon(50% 0%,100% 50%,50% 100%,0% 50%)" }} />
          </span>
          on your TBR
        </span>
        <span className="flex items-center gap-1.5" style={{ color: "#8a6c20" }}>
          <span style={{ fontSize: 10 }}>✦</span>
          hidden gem
        </span>
      </div>
      <p className="text-[11px] -mt-1" style={{ color: "var(--text-3)" }}>
        Click a cluster to see its books · Click a model card to highlight its picks
      </p>

      {/* Canvas + side panel */}
      <div className="flex gap-4 items-stretch">
        <div className="relative flex-1 min-w-0 rounded-2xl overflow-hidden"
          style={{ background: "var(--surface)", border: "1px solid var(--border-mid)", boxShadow: "0 2px 12px rgba(139,107,70,0.06)" }}>
          <svg ref={svgRef} className="w-full" style={{ height: 500 }} />

          {tooltip && (
            <div className="absolute z-20 pointer-events-none rounded-xl px-3 py-2.5 text-xs shadow-lg"
              style={{ left: Math.min(tooltip.x + 14, 350), top: Math.max(tooltip.y - 60, 8), background: "var(--surface)", border: "1px solid var(--border-mid)", maxWidth: 220, backdropFilter: "blur(8px)" }}>
              {tooltip.type === "book" && (
                <>
                  <div className="font-medium leading-snug" style={{ color: "var(--text-1)" }}>{tooltip.content.title}</div>
                  <div className="mt-0.5" style={{ color: "var(--text-2)" }}>{tooltip.content.author}</div>
                  {tooltip.content.my_rating > 0 && (
                    <div className="mt-1 text-[11px]" style={{ color: "var(--sand)" }}>
                      {"★".repeat(tooltip.content.my_rating)}<span style={{ color: "var(--border-mid)" }}>{"★".repeat(5 - tooltip.content.my_rating)}</span>
                    </div>
                  )}
                </>
              )}
              {tooltip.type === "rec" && (() => {
                const r = tooltip.content;
                const labelColor = r.isConsensus ? BOTH_COLOR : (MODEL_COLORS[r.model_name || ""] || "var(--text-2)");
                return (
                  <>
                    <div className="text-[10px] uppercase tracking-widest font-semibold mb-1.5" style={{ color: labelColor }}>
                      {r.isConsensus ? "Both models" : r.model_name}{r.comfort_zone === false ? " · outside comfort zone" : ""}
                    </div>
                    <div className="flex gap-2.5 items-start">
                      <BookCover isbn={r.isbn} title={r.title} size={44} />
                      <div className="min-w-0">
                        <div className="font-medium leading-snug" style={{ color: "var(--text-1)" }}>{r.title}</div>
                        <div className="mt-0.5" style={{ color: "var(--text-2)" }}>{r.author}</div>
                      </div>
                    </div>
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {r.on_tbr && (
                        <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full"
                          style={{ background: "rgba(90,138,90,0.12)", color: "var(--sage-dark)", border: "1px solid rgba(90,138,90,0.25)" }}>
                          on your TBR
                        </span>
                      )}
                      {r.hidden_gem && (
                        <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full"
                          style={{ background: "rgba(196,160,80,0.12)", color: "#8a6c20", border: "1px solid rgba(196,160,80,0.35)" }}>
                          ✦ hidden gem · likely no holds
                        </span>
                      )}
                    </div>
                    {r.why && <div className="mt-1.5 text-[10px] leading-snug" style={{ color: "var(--text-3)" }}>{r.why}</div>}
                  </>
                );
              })()}
              {tooltip.type === "genre" && (
                <>
                  <div className="font-medium" style={{ color: "var(--text-1)" }}>{tooltip.content.name}</div>
                  <div className="text-[10px] mt-0.5" style={{ color: "var(--text-3)" }}>Click to see nearest AI picks</div>
                </>
              )}
            </div>
          )}
        </div>

        {/* Side panel */}
        {showPanel && (
          <div className="w-56 shrink-0 rounded-2xl p-4 overflow-y-auto"
            style={{ maxHeight: 500, background: "var(--surface)", border: "1px solid var(--border-mid)", boxShadow: "0 2px 12px rgba(139,107,70,0.06)" }}>
            {expandedCluster !== null && (
              <>
                <div className="flex items-center justify-between mb-3">
                  <div className="text-[10px] font-semibold uppercase tracking-widest" style={{ color: clusters.get(expandedCluster)?.color }}>
                    {expandedClusterName}
                  </div>
                  <span className="text-[10px]" style={{ color: "var(--text-3)" }}>{expandedBooks.length} books</span>
                </div>
                <div className="space-y-3">
                  {expandedBooks.map((b, i) => (
                    <div key={i} className="pb-3 border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                      <div className="text-xs font-medium leading-snug" style={{ color: "var(--text-1)" }}>{b.title}</div>
                      <div className="text-[11px] mt-0.5" style={{ color: "var(--text-2)" }}>{b.author}</div>
                      {b.my_rating > 0 && (
                        <div className="text-[10px] mt-0.5" style={{ color: "var(--sand)" }}>
                          {"★".repeat(b.my_rating)}<span style={{ color: "var(--border-mid)" }}>{"★".repeat(5 - b.my_rating)}</span>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}
            {selectedGenre !== null && (
              <>
                <div className="text-[10px] font-semibold uppercase tracking-widest mb-2" style={{ color: "var(--text-2)" }}>{selectedGenre.name}</div>
                {genreNearRecs.length > 0 ? (
                  <div className="space-y-3">
                    <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--text-3)" }}>Nearest AI picks</div>
                    {genreNearRecs.map((r, i) => {
                      const color = r.isConsensus ? BOTH_COLOR : (MODEL_COLORS[r.model_name || ""] || "var(--text-2)");
                      const libby = libbyData?.[r.isbn || ""];
                      return (
                        <div key={i} className="pb-3 border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                          <div className="text-[10px] font-semibold mb-1" style={{ color }}>{r.isConsensus ? "Both models" : r.model_name}</div>
                          <div className="flex gap-2 items-start">
                            <BookCover isbn={r.isbn} title={r.title} size={36} />
                            <div className="min-w-0">
                              <div className="text-xs font-medium leading-snug" style={{ color: "var(--text-1)" }}>{r.title}</div>
                              <div className="text-[11px] mt-0.5" style={{ color: "var(--text-2)" }}>{r.author}</div>
                              {r.why && <div className="text-[10px] mt-1 leading-snug" style={{ color: "var(--text-3)" }}>{r.why}</div>}
                              {libby?.available && <div className="text-[10px] mt-1" style={{ color: "var(--sage)" }}>Available on Libby</div>}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : <div className="text-[10px] italic" style={{ color: "var(--text-3)" }}>No AI picks mapped here yet.</div>}
              </>
            )}
          </div>
        )}
      </div>

      {/* Model cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
        {modelEntries.map(([name, m]) => {
          const color = MODEL_COLORS[name] || "var(--sage)";
          const isActive = activeModel === name;
          const recCount = dedupedRecs.filter(r => !r.isConsensus && r.model_name === name).length;
          const consensusCount = dedupedRecs.filter(r => r.isConsensus).length;
          return (
            <button key={name} onClick={() => setActiveModel(isActive ? null : name)}
              className="text-left rounded-2xl p-5 transition-all"
              style={{
                background: isActive ? `${color}12` : "var(--surface)",
                borderTop:    `1px solid ${isActive ? color + "50" : "var(--border)"}`,
                borderRight:  `1px solid ${isActive ? color + "50" : "var(--border)"}`,
                borderBottom: `1px solid ${isActive ? color + "50" : "var(--border)"}`,
                borderLeft:   `3px solid ${color}`,
                boxShadow: isActive ? `0 2px 12px ${color}18` : "0 1px 6px rgba(139,107,70,0.04)",
              }}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 shrink-0" style={{ background: color, clipPath: "polygon(50% 0%,100% 50%,50% 100%,0% 50%)" }} />
                  <span className="font-medium text-sm" style={{ color: "var(--text-1)" }}>{name}</span>
                </div>
                <span className="text-[10px] px-2 py-0.5 rounded-full"
                  style={{ background: isActive ? `${color}18` : "var(--surface-2)", color: isActive ? color : "var(--text-3)", border: `1px solid ${isActive ? color + "40" : "var(--border)"}` }}>
                  {isActive ? "highlighting" : "click to filter"}
                </span>
              </div>
              {m.info?.description && <p className="text-[11px] leading-relaxed mb-3" style={{ color: "var(--text-2)" }}>{m.info.description}</p>}
              {m.meta && (() => {
                const { ttft_ms, generation_ms, latency_ms, prompt_tokens, completion_tokens } = m.meta;
                const tokPerSec = completion_tokens && generation_ms ? Math.round(completion_tokens / (generation_ms / 1000)) : null;
                const tokPerRec = completion_tokens ? Math.round(completion_tokens / 5) : null;
                const ttftPct   = ttft_ms && latency_ms ? (ttft_ms / latency_ms) * 100 : null;
                const genPct    = generation_ms && latency_ms ? (generation_ms / latency_ms) * 100 : null;
                return (
                  <div className="mb-3 space-y-2">
                    {/* Latency breakdown bar */}
                    {ttftPct !== null && genPct !== null && (
                      <div>
                        <div className="flex justify-between text-[9px] mb-0.5" style={{ color: "var(--text-3)" }}>
                          <span>wall-clock breakdown</span>
                          <span className="font-mono">{latency_ms.toLocaleString()} ms total</span>
                        </div>
                        <div className="flex h-1.5 rounded-full overflow-hidden w-full" style={{ background: "var(--border-mid)" }}>
                          <div style={{ width: `${ttftPct}%`, background: color, opacity: 0.5 }} title={`TTFT: ${ttft_ms} ms`} />
                          <div style={{ width: `${genPct}%`, background: color }} title={`Generation: ${generation_ms} ms`} />
                        </div>
                        <div className="flex gap-3 mt-0.5 text-[9px]" style={{ color: "var(--text-3)" }}>
                          <span><span style={{ background: color, opacity: 0.5, display: "inline-block", width: 6, height: 6, borderRadius: 1, marginRight: 3 }} />{ttft_ms} ms TTFT</span>
                          <span><span style={{ background: color, display: "inline-block", width: 6, height: 6, borderRadius: 1, marginRight: 3 }} />{generation_ms} ms gen</span>
                        </div>
                      </div>
                    )}
                    {/* Token throughput */}
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
                      {prompt_tokens != null && <div><span className="font-mono font-semibold" style={{ color: "var(--text-2)" }}>{prompt_tokens.toLocaleString()}</span><span className="ml-1" style={{ color: "var(--text-3)" }}>prompt tkns</span></div>}
                      {completion_tokens != null && <div><span className="font-mono font-semibold" style={{ color: "var(--text-2)" }}>{completion_tokens.toLocaleString()}</span><span className="ml-1" style={{ color: "var(--text-3)" }}>output tkns</span></div>}
                      {tokPerRec != null && <div title="average output tokens per recommendation — higher = more verbose"><span className="font-mono font-semibold" style={{ color: "var(--text-2)" }}>{tokPerRec}</span><span className="ml-1" style={{ color: "var(--text-3)" }}>tokens/rec</span></div>}
                      {tokPerSec != null && <div title="decode speed — tokens generated per second"><span className="font-mono font-semibold" style={{ color }}>{tokPerSec.toLocaleString()}</span><span className="ml-1" style={{ color: "var(--text-3)" }}>tokens/sec</span></div>}
                    </div>
                  </div>
                );
              })()}
              <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-[11px]">
                <div><span className="font-mono font-semibold" style={{ color: "var(--text-2)" }}>{recCount}</span><span className="ml-1" style={{ color: "var(--text-3)" }}>unique picks</span></div>
                {consensusCount > 0 && <div><span className="font-mono font-semibold" style={{ color: BOTH_COLOR }}>{consensusCount}</span><span className="ml-1" style={{ color: "var(--text-3)" }}>both agreed</span></div>}
                {(() => { const gems = (m.recommendations || []).filter((r: any) => r.hidden_gem).length; return gems > 0 ? <div><span className="font-mono font-semibold" style={{ color: "#8a6c20" }}>{gems}</span><span className="ml-1" style={{ color: "var(--text-3)" }}>hidden gem{gems > 1 ? "s" : ""}</span></div> : null; })()}
              </div>
              {m.error && <div className="text-[10px] mt-2" style={{ color: "var(--rust)" }}>Error: {m.error}</div>}
            </button>
          );
        })}
      </div>

      {/* Dynamic performance insight panel */}
      {(() => {
        const entries = modelEntries.filter(([, m]) => m.meta);
        if (entries.length < 2) return null;

        const gptEntry = entries.find(([n]) => n.includes("GPT"));
        const glmEntry = entries.find(([n]) => n.includes("GLM"));
        if (!gptEntry || !glmEntry) return null;

        const [gptName, gpt] = gptEntry;
        const [glmName, glm] = glmEntry;
        const gm = gpt.meta!; const lm = glm.meta!;

        const gptTokSec = gm.completion_tokens && gm.generation_ms ? Math.round(gm.completion_tokens / (gm.generation_ms / 1000)) : null;
        const glmTokSec = lm.completion_tokens && lm.generation_ms ? Math.round(lm.completion_tokens / (lm.generation_ms / 1000)) : null;
        const gptTokRec = gm.completion_tokens ? Math.round(gm.completion_tokens / 5) : null;
        const glmTokRec = lm.completion_tokens ? Math.round(lm.completion_tokens / 5) : null;

        const insights: { label: string; text: string }[] = [];

        // TTFT story
        if (gm.ttft_ms && lm.ttft_ms) {
          const ratio = gm.ttft_ms / lm.ttft_ms;
          if (ratio > 2) {
            insights.push({
              label: "GPT-OSS long TTFT",
              text: `GPT-OSS waited ${(gm.ttft_ms / 1000).toFixed(1)}s before its first token — ${ratio.toFixed(1)}× longer than GLM. This isn't Cerebras being slow: GPT-OSS is a reasoning model that runs an internal chain-of-thought before generating output. The long TTFT is the model thinking.`,
            });
          } else if (lm.ttft_ms / gm.ttft_ms > 2) {
            insights.push({
              label: "GLM long TTFT",
              text: `GLM took ${(lm.ttft_ms / 1000).toFixed(1)}s to first token — ${(lm.ttft_ms / gm.ttft_ms).toFixed(1)}× slower than GPT-OSS off the mark.`,
            });
          } else {
            insights.push({
              label: "Similar TTFT",
              text: `Both models had similar time-to-first-token (${gm.ttft_ms}ms vs ${lm.ttft_ms}ms) — neither was queued. Cerebras's wafer-scale chip delivers consistent first-token latency across both.`,
            });
          }
        }

        // Throughput story
        if (gptTokSec && glmTokSec) {
          const faster = gptTokSec > glmTokSec ? gptName : glmName;
          const slowerTok = Math.min(gptTokSec, glmTokSec);
          const fasterTok = Math.max(gptTokSec, glmTokSec);
          const ratio = fasterTok / slowerTok;
          insights.push({
            label: "Decode throughput",
            text: `Once past the first token, ${faster} decoded ${ratio.toFixed(1)}× faster (${fasterTok.toLocaleString()} vs ${slowerTok.toLocaleString()} tok/s). ${gptTokSec > glmTokSec ? "GPT-OSS's 5.1B active parameters per token — despite 117B total — means less computation per decode step." : "GLM's 32B active parameters make it lighter per step than its 355B total suggest."}`,
          });
        }

        // Verbosity vs throughput
        if (gptTokRec && glmTokRec) {
          const diff = Math.abs(gptTokRec - glmTokRec);
          const pct = diff / Math.min(gptTokRec, glmTokRec);
          if (pct < 0.2) {
            insights.push({
              label: "Verbosity matched",
              text: `Both models wrote similarly long recommendations (~${gptTokRec} vs ~${glmTokRec} tokens each), so the speed difference is pure throughput — not one model writing more.`,
            });
          } else {
            const verbose = gptTokRec > glmTokRec ? gptName : glmName;
            const terse = gptTokRec > glmTokRec ? glmName : gptName;
            const vTok = Math.max(gptTokRec, glmTokRec);
            const tTok = Math.min(gptTokRec, glmTokRec);
            insights.push({
              label: "Verbosity gap",
              text: `${verbose} wrote ~${vTok} tokens per recommendation vs ${terse}'s ~${tTok}. Some of the generation time gap is ${verbose} being more verbose, not just slower.`,
            });
          }
        }

        // Architecture + task fit note
        insights.push({
          label: "Task fit",
          text: `GPT-OSS is a reasoning model built for math and code (chain-of-thought tasks where a minute on GPU = 1 second on Cerebras). It's overbuilt for book recommendations. GLM 4.7 is optimised for interactive, agentic tasks — a closer match here. Model-task fit matters as much as raw speed.`,
        });

        return (
          <div className="rounded-2xl p-5 space-y-4"
            style={{ background: "var(--surface)", border: "1px solid var(--border-mid)", boxShadow: "0 1px 6px rgba(139,107,70,0.04)" }}>
            <div className="text-[10px] tracking-[0.18em] uppercase font-medium" style={{ color: "var(--text-3)" }}>
              What does this data mean?
            </div>
            <div className="space-y-3">
              {insights.map(({ label, text }) => (
                <div key={label} className="flex gap-3">
                  <div className="mt-0.5 shrink-0 w-1.5 h-1.5 rounded-full" style={{ background: "var(--sage)", marginTop: 5 }} />
                  <div>
                    <span className="text-[11px] font-semibold" style={{ color: "var(--text-1)" }}>{label}. </span>
                    <span className="text-[11px] leading-relaxed" style={{ color: "var(--text-2)" }}>{text}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })()}

      {/* Judge panel — opt-in */}
      {!battle.judge && !judgeLoading && (
        <div className="rounded-2xl flex items-center justify-between px-5 py-4"
          style={{ background: "var(--surface)", border: "1px solid var(--border-mid)", boxShadow: "0 1px 6px rgba(139,107,70,0.04)" }}>
          <div>
            <div className="text-[10px] tracking-[0.18em] uppercase font-medium mb-0.5" style={{ color: "var(--text-3)" }}>Judge</div>
            <div className="text-xs" style={{ color: "var(--text-2)" }}>Qwen 2.5 7B evaluates each model's reasoning quality — runs locally via Ollama (~2 min)</div>
            {judgeError && <div className="text-xs mt-1" style={{ color: "var(--rust)" }}>{judgeError} — is Ollama running?</div>}
          </div>
          <button onClick={onRunJudge}
            className="ml-4 shrink-0 text-xs font-medium px-4 py-2 rounded-xl transition-all"
            style={{ background: "rgba(90,138,90,0.10)", color: "var(--sage-dark)", border: "1px solid rgba(90,138,90,0.30)", boxShadow: "0 1px 4px rgba(90,138,90,0.12)" }}
            onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(90,138,90,0.18)")}
            onMouseLeave={(e) => (e.currentTarget.style.background = "rgba(90,138,90,0.10)")}>
            Run Judge
          </button>
        </div>
      )}

      {judgeLoading && (
        <div className="rounded-2xl flex items-center gap-3 px-5 py-4"
          style={{ background: "var(--surface)", border: "1px solid var(--border-mid)" }}>
          <div className="w-4 h-4 rounded-full border-2 border-t-transparent animate-spin shrink-0"
            style={{ borderColor: "var(--sage) transparent var(--sage) var(--sage)" }} />
          <div>
            <div className="text-[10px] tracking-[0.18em] uppercase font-medium mb-0.5" style={{ color: "var(--text-3)" }}>Judge</div>
            <span className="text-xs" style={{ color: "var(--text-2)" }}>Qwen 2.5 7B is reading both models' reasoning — ~2 min on local hardware…</span>
          </div>
        </div>
      )}

      {battle.judge && !("error" in battle.judge) && (() => {
        const SCORE_LABELS: Record<string, string> = { relevance: "Relevance", reasoning_depth: "Reasoning depth", novelty: "Novelty", specificity: "Specificity" };
        const jNames = Object.keys(battle.models);
        const judgeModel = Object.values(battle.judge)[0]?.model ?? "qwen2.5:7b";
        return (
          <div className="rounded-2xl overflow-hidden" style={{ border: "1px solid var(--border-mid)", boxShadow: "0 2px 12px rgba(139,107,70,0.06)" }}>
            <div className="flex items-center justify-between px-5 py-3" style={{ background: "var(--surface-2)", borderBottom: "1px solid var(--border)" }}>
              <div className="flex items-center gap-2">
                <span className="text-[10px] tracking-[0.18em] uppercase font-medium" style={{ color: "var(--text-3)" }}>Judge</span>
                <span className="text-[10px] px-2 py-0.5 rounded-full" style={{ color: "var(--text-2)", background: "var(--surface)", border: "1px solid var(--border)" }}>{judgeModel} · local</span>
              </div>
              {battle.winner && (
                <div className="flex items-center gap-2 text-xs">
                  <span style={{ color: "var(--text-3)" }}>winner</span>
                  <span className="font-medium" style={{ color: MODEL_COLORS[battle.winner as keyof typeof MODEL_COLORS] ?? "var(--sage)" }}>{battle.winner}</span>
                </div>
              )}
            </div>
            <div className="grid grid-cols-2" style={{ background: "var(--surface)", borderTop: "1px solid var(--border)" }}>
              {jNames.map((name) => {
                const j = battle.judge![name];
                if (!j || j.error) return <div key={name} className="p-5 text-xs" style={{ color: "var(--rust)" }}>{j?.error ?? "No verdict"}</div>;
                const color = MODEL_COLORS[name as keyof typeof MODEL_COLORS] ?? "var(--sage)";
                const scores = j.scores ?? {};
                const avg = Object.values(scores).length ? (Object.values(scores).reduce((a, b) => a + b, 0) / Object.values(scores).length).toFixed(1) : null;
                const isWinner = battle.winner === name;
                return (
                  <div key={name} className="p-5 space-y-4" style={{ borderRight: name === jNames[0] ? "1px solid var(--border)" : "none" }}>
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium" style={{ color }}>{name}</span>
                      {avg && <span className="text-sm font-semibold font-mono" style={{ color: isWinner ? color : "var(--text-3)" }}>{avg}<span className="text-[10px]" style={{ color: "var(--text-3)" }}>/10</span></span>}
                    </div>
                    <div className="space-y-2">
                      {Object.entries(SCORE_LABELS).map(([key, label]) => {
                        const val = scores[key] ?? 0;
                        return (
                          <div key={key}>
                            <div className="flex justify-between text-[10px] mb-1" style={{ color: "var(--text-3)" }}>
                              <span>{label}</span><span className="font-mono" style={{ color: "var(--text-2)" }}>{val}/10</span>
                            </div>
                            <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--border-mid)" }}>
                              <div className="h-full rounded-full transition-all duration-700" style={{ width: `${val * 10}%`, background: color, opacity: isWinner ? 1 : 0.45 }} />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    {j.verdict && <p className="text-[11px] leading-relaxed pt-3" style={{ color: "var(--text-2)", borderTop: "1px solid var(--border)" }}>{j.verdict}</p>}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}
    </section>
  );
}
