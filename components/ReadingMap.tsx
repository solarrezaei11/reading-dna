"use client";

import { useEffect, useRef, useState } from "react";
import * as d3 from "d3";

type Point = {
  title: string;
  author: string;
  my_rating: number;
  cluster_id: number;
  cluster_name: string;
  x: number;
  y: number;
};

type Props = { points: Point[] };

const CLUSTER_COLORS = [
  "#a855f7", "#3b82f6", "#10b981", "#f59e0b", "#ef4444",
];

export default function ReadingMap({ points }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [selectedCluster, setSelectedCluster] = useState<number | null>(null);
  const [tooltip, setTooltip] = useState<{ book: Point; x: number; y: number } | null>(null);

  const clusterIds = [...new Set(points.map((p) => p.cluster_id))].sort();
  const activePoints = selectedCluster === null ? points : points.filter(p => p.cluster_id === selectedCluster);

  useEffect(() => {
    if (!svgRef.current || !points.length) return;
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const W = svgRef.current.clientWidth || 500;
    const H = 400;
    const PAD = 48;
    svg.attr("viewBox", `0 0 ${W} ${H}`);

    // coords already normalized [0,1] from backend
    const xScale = d3.scaleLinear().domain([0, 1]).range([PAD, W - PAD]);
    const yScale = d3.scaleLinear().domain([0, 1]).range([H - PAD, PAD]);

    // Log scale for dot size: differences feel natural, not extreme
    const rScale = d3.scaleLog().domain([1, 5]).range([5, 14]).clamp(true);

    // Cluster centroids for labels
    const clusterIds = [...new Set(points.map(d => d.cluster_id))];
    clusterIds.forEach((cid) => {
      const pts = points.filter(d => d.cluster_id === cid);
      const cx = d3.mean(pts, d => xScale(d.x)) ?? 0;
      const cy = d3.mean(pts, d => yScale(d.y)) ?? 0;
      const name = pts[0]?.cluster_name ?? "";
      const color = CLUSTER_COLORS[cid % CLUSTER_COLORS.length];
      const dimmed = selectedCluster !== null && selectedCluster !== cid;

      svg.append("text")
        .attr("x", cx)
        .attr("y", cy - 20)
        .attr("text-anchor", "middle")
        .attr("fill", color)
        .attr("font-size", 11)
        .attr("font-weight", "600")
        .attr("opacity", dimmed ? 0.15 : 0.9)
        .attr("font-family", "var(--font-geist-sans)")
        .text(name);
    });

    // Draw dots
    svg.selectAll("circle")
      .data(points)
      .enter()
      .append("circle")
      .attr("cx", (d) => xScale(d.x))
      .attr("cy", (d) => yScale(d.y))
      .attr("r", (d) => rScale(Math.max(1, d.my_rating)))
      .attr("fill", (d) => CLUSTER_COLORS[d.cluster_id % CLUSTER_COLORS.length])
      .attr("opacity", (d) =>
        selectedCluster === null || selectedCluster === d.cluster_id ? 0.85 : 0.08
      )
      .attr("stroke", "rgba(255,255,255,0.15)")
      .attr("stroke-width", 1)
      .attr("cursor", "pointer")
      .on("mouseenter", (event, d) => {
        const rect = svgRef.current!.getBoundingClientRect();
        setTooltip({ book: d, x: event.clientX - rect.left, y: event.clientY - rect.top });
        d3.select(event.currentTarget).attr("stroke", "white").attr("stroke-width", 2);
      })
      .on("mouseleave", (event) => {
        setTooltip(null);
        d3.select(event.currentTarget).attr("stroke", "rgba(255,255,255,0.15)").attr("stroke-width", 1);
      });

  }, [points, selectedCluster]);

  const booksInCluster = selectedCluster !== null
    ? points.filter(p => p.cluster_id === selectedCluster).sort((a, b) => b.my_rating - a.my_rating)
    : [];

  return (
    <section className="space-y-4">
      <h2 className="text-lg font-semibold text-zinc-300">Reading Map</h2>

      {/* Cluster pills */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setSelectedCluster(null)}
          className={`px-3 py-1 rounded-full text-xs transition-colors border ${
            selectedCluster === null
              ? "bg-white/15 border-white/30 text-white"
              : "bg-white/5 border-white/10 text-zinc-400 hover:text-white"
          }`}
        >
          All clusters
        </button>
        {clusterIds.map((cid) => {
          const name = points.find(p => p.cluster_id === cid)?.cluster_name ?? "";
          const color = CLUSTER_COLORS[cid % CLUSTER_COLORS.length];
          return (
            <button
              key={cid}
              onClick={() => setSelectedCluster(selectedCluster === cid ? null : cid)}
              className={`px-3 py-1 rounded-full text-xs transition-colors border ${
                selectedCluster === cid
                  ? "text-white"
                  : "bg-white/5 border-white/10 text-zinc-400 hover:text-white"
              }`}
              style={selectedCluster === cid ? { backgroundColor: color + "33", borderColor: color + "88", color } : {}}
            >
              <span className="inline-block w-1.5 h-1.5 rounded-full mr-1.5 align-middle" style={{ backgroundColor: color }} />
              {name}
            </button>
          );
        })}
      </div>

      <div className="flex gap-4">
        {/* Map */}
        <div className="bg-white/5 border border-white/10 rounded-2xl p-3 relative flex-1 min-w-0">
          <svg ref={svgRef} className="w-full" style={{ height: 380 }} />
          {tooltip && (
            <div
              className="absolute z-10 pointer-events-none bg-[#1a1a2e] border border-white/20 rounded-xl px-3 py-2 text-xs shadow-xl max-w-[180px]"
              style={{ left: Math.min(tooltip.x + 12, 260), top: Math.max(tooltip.y - 40, 8) }}
            >
              <div className="font-medium text-white leading-snug">{tooltip.book.title}</div>
              <div className="text-zinc-400 mt-0.5">{tooltip.book.author}</div>
              <div className="text-yellow-400 mt-1 tracking-tight">{"★".repeat(tooltip.book.my_rating)}{"☆".repeat(5 - tooltip.book.my_rating)}</div>
            </div>
          )}
        </div>

        {/* Book list for selected cluster */}
        {selectedCluster !== null && booksInCluster.length > 0 && (
          <div className="w-56 shrink-0 bg-white/5 border border-white/10 rounded-2xl p-4 space-y-3 overflow-y-auto" style={{ maxHeight: 406 }}>
            <div className="text-xs text-zinc-400 font-mono tracking-widest uppercase">
              {booksInCluster.length} books
            </div>
            {booksInCluster.map((b, i) => (
              <div key={i} className="space-y-0.5">
                <div className="text-xs font-medium text-white leading-snug">{b.title}</div>
                <div className="text-[10px] text-zinc-500">{b.author}</div>
                <div className="text-[10px] text-yellow-500">{"★".repeat(b.my_rating)}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      <p className="text-xs text-zinc-600">Dot size = your rating · Click a cluster to explore its books · Similar books cluster together</p>
    </section>
  );
}
