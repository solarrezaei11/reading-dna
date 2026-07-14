"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as d3 from "d3";
import { BookCover } from "./BookCover";
import { clamp, normalizeMapRecommendations, normalizeRecommendations, type MapRecommendation, type NormalizedRecommendation } from "@/lib/recommendations";
import type { BattleResult, GenreAnchor, JudgeVerdict, LibbyResponse, MapData, MapPoint } from "@/lib/types";

type Props = {
  mapData: MapData | null;
  mapError?: string;
  battle: BattleResult & { judge?: Record<string, JudgeVerdict> };
  libbyData?: LibbyResponse | null;
  libbyError?: string;
  library?: string;
  judgeLoading?: boolean;
  judgeError?: string;
  onRunJudge?: () => void;
  onRetryMap?: () => void;
  onRetryLibby?: () => void;
};

type Tooltip =
  | { point: MapPoint; type: "book"; x: number; y: number }
  | { point: MapRecommendation; type: "recommendation"; x: number; y: number };
type Cluster = { id: number; name: string; x: number; y: number; books: MapPoint[] };

const COLORS = ["#4a8c7e", "#9b7040", "#7a6a8c", "#b06040", "#5a8a5a"];
const CONSENSUS_COLOR = "#5a8a5a";

function colorForModel(name: string, index = 0): string {
  if (name.includes("GPT")) return "#4a8c7e";
  if (name.includes("GLM")) return "#9b7040";
  return COLORS[index % COLORS.length];
}

function scoreEntries(verdict: JudgeVerdict | undefined): Array<[string, number]> {
  return Object.entries(verdict?.scores ?? {}).filter((entry): entry is [string, number] => typeof entry[1] === "number");
}

function availabilityText(response: LibbyResponse | null | undefined, isbn: string | undefined): string {
  if (!response || !isbn) return "Availability not checked";
  const availability = response.results[isbn];
  if (!availability) return "No availability result";
  if (availability.available || availability.status === "available") return "Available on Libby";
  if (availability.status === "waitlist") {
    return availability.wait_weeks ? `Libby waitlist: about ${availability.wait_weeks} week${availability.wait_weeks === 1 ? "" : "s"}` : "Libby waitlist";
  }
  if (availability.status === "not_in_catalog") return "Not in this library's ebook catalog";
  if (availability.status === "invalid_isbn") return "Availability unavailable: invalid ISBN";
  if (availability.status === "error") return "Libby availability check failed";
  return "Currently unavailable on Libby";
}

function MapCanvas({
  mapData,
  recommendations,
  modelNames,
}: {
  mapData: MapData;
  recommendations: MapRecommendation[];
  modelNames: string[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [width, setWidth] = useState(0);
  const [activeModel, setActiveModel] = useState<string | null>(null);
  const [selectedCluster, setSelectedCluster] = useState<number | null>(null);
  const [selectedGenre, setSelectedGenre] = useState<GenreAnchor | null>(null);
  const [tooltip, setTooltip] = useState<Tooltip | null>(null);

  const clusters = useMemo<Cluster[]>(() => {
    const grouped = new Map<number, MapPoint[]>();
    mapData.points.forEach((point) => {
      const books = grouped.get(point.cluster_id);
      if (books) {
        books.push(point);
      } else {
        grouped.set(point.cluster_id, [point]);
      }
    });
    return [...grouped.entries()].map(([id, books]) => ({
      id,
      name: books[0]?.cluster_name ?? `Cluster ${id + 1}`,
      x: books.reduce((sum, book) => sum + book.x, 0) / books.length,
      y: books.reduce((sum, book) => sum + book.y, 0) / books.length,
      books,
    }));
  }, [mapData.points]);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const update = () => {
      const next = Math.floor(element.getBoundingClientRect().width);
      setWidth((current) => next > 0 && next !== current ? next : current);
    };
    update();
    if (!("ResizeObserver" in window)) return;
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const node = svgRef.current;
    if (!node || width === 0) return;
    const height = 430;
    const pad = 44;
    const svg = d3.select(node);
    svg.selectAll("*").remove();
    svg.attr("viewBox", `0 0 ${width} ${height}`);
    const x = d3.scaleLinear().domain([0, 1]).range([pad, width - pad]).clamp(true);
    const y = d3.scaleLinear().domain([0, 1]).range([height - pad, pad]).clamp(true);
    const localPosition = (event: MouseEvent) => {
      const rect = node.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };

    mapData.genre_anchors.forEach((genre) => {
      const selected = selectedGenre?.name === genre.name;
      const group = svg.append("g").attr("role", "button").attr("tabindex", activeModel ? -1 : 0)
        .attr("aria-label", `${genre.name} genre territory`)
        .style("cursor", activeModel ? "default" : "pointer");
      const select = () => {
        if (activeModel) return;
        setSelectedGenre((current) => current?.name === genre.name ? null : genre);
        setSelectedCluster(null);
      };
      group.append("circle").attr("cx", x(genre.x)).attr("cy", y(genre.y)).attr("r", 40)
        .attr("fill", selected ? "rgba(90,138,90,0.12)" : "rgba(139,107,70,0.04)")
        .attr("stroke", selected ? "rgba(90,138,90,0.55)" : "rgba(139,107,70,0.22)")
        .attr("stroke-dasharray", "4 3");
      group.append("text").attr("x", x(genre.x)).attr("y", y(genre.y) + 3).attr("text-anchor", "middle")
        .attr("font-size", 9).attr("fill", "var(--text-3)").text(genre.name);
      group.on("click", select).on("keydown", (event: KeyboardEvent) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(); }
      });
    });

    clusters.forEach((cluster, clusterIndex) => {
      const centerX = x(cluster.x);
      const centerY = y(cluster.y);
      const radius = clamp(Math.sqrt(cluster.books.length) * 9, 30, 70);
      const expanded = selectedCluster === cluster.id;
      const color = COLORS[clusterIndex % COLORS.length];
      const group = svg.append("g").attr("role", "button").attr("tabindex", 0)
        .attr("aria-label", `${cluster.name}, ${cluster.books.length} books. Activate to ${expanded ? "collapse" : "expand"}.`)
        .style("cursor", "pointer");
      const select = () => {
        setSelectedCluster((current) => current === cluster.id ? null : cluster.id);
        setSelectedGenre(null);
      };
      group.append("circle").attr("cx", centerX).attr("cy", centerY).attr("r", radius)
        .attr("fill", color).attr("fill-opacity", expanded ? 0.12 : activeModel ? 0.05 : 0.16)
        .attr("stroke", color).attr("stroke-opacity", activeModel ? 0.2 : 0.65).attr("stroke-width", 1.3);
      group.append("text").attr("x", centerX).attr("y", centerY - 4).attr("text-anchor", "middle")
        .attr("font-size", 10).attr("font-weight", 600).attr("fill", color).text(cluster.name);
      group.append("text").attr("x", centerX).attr("y", centerY + 10).attr("text-anchor", "middle")
        .attr("font-size", 8).attr("fill", color).text(`${cluster.books.length} books`);
      group.on("click", select).on("keydown", (event: KeyboardEvent) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(); }
      });
      if (expanded) {
        cluster.books.forEach((book) => {
          const circle = svg.append("circle").attr("cx", x(book.x)).attr("cy", y(book.y))
            .attr("r", clamp(book.my_rating * 1.8, 3, 10)).attr("fill", color).attr("stroke", "#fff")
            .attr("stroke-width", 1).attr("role", "img").attr("aria-label", `${book.title} by ${book.author ?? "unknown author"}`)
            .style("cursor", "help");
          circle.on("mouseenter", (event: MouseEvent) => {
            const position = localPosition(event);
            setTooltip({ type: "book", point: book, ...position });
          }).on("mouseleave", () => setTooltip(null));
        });
      }
    });

    recommendations.forEach((recommendation, index) => {
      const selected = !activeModel || recommendation.isConsensus || recommendation.models.includes(activeModel);
      const color = recommendation.isConsensus ? CONSENSUS_COLOR : colorForModel(recommendation.models[0] ?? "", index);
      const centerX = x(recommendation.x);
      const centerY = y(recommendation.y);
      const diamond = `M${centerX},${centerY - 7} L${centerX + 7},${centerY} L${centerX},${centerY + 7} L${centerX - 7},${centerY}Z`;
      svg.append("path").attr("d", diamond).attr("fill", color).attr("fill-opacity", selected ? 0.95 : 0.1)
        .attr("stroke", recommendation.comfort_zone === false ? color : "rgba(0,0,0,0.15)")
        .attr("stroke-width", recommendation.comfort_zone === false ? 2 : 0.5).attr("pointer-events", "none");
      const hitArea = svg.append("path").attr("d", `M${centerX},${centerY - 13} L${centerX + 13},${centerY} L${centerX},${centerY + 13} L${centerX - 13},${centerY}Z`)
        .attr("fill", "transparent").attr("role", "img")
        .attr("aria-label", `${recommendation.title}, recommended by ${recommendation.models.join(" and ")}`)
        .style("cursor", "help");
      hitArea.on("mouseenter", (event: MouseEvent) => {
        if (!selected) return;
        const position = localPosition(event);
        setTooltip({ type: "recommendation", point: recommendation, ...position });
      }).on("mouseleave", () => setTooltip(null));
    });
  }, [activeModel, clusters, mapData, modelNames, recommendations, selectedCluster, selectedGenre, width]);

  const nearby = selectedGenre
    ? recommendations.map((recommendation) => ({ recommendation, distance: Math.hypot(recommendation.x - selectedGenre.x, recommendation.y - selectedGenre.y) }))
      .sort((a, b) => a.distance - b.distance).slice(0, 3)
    : [];
  const selectedBooks = clusters.find((cluster) => cluster.id === selectedCluster)?.books ?? [];
  const tooltipLeft = tooltip ? clamp(tooltip.x + 12, 8, Math.max(8, width - 228)) : 0;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 text-[11px]" style={{ color: "var(--text-3)" }}>
        {modelNames.map((name, index) => (
          <button key={name} type="button" onClick={() => setActiveModel((current) => current === name ? null : name)}
            aria-pressed={activeModel === name} className="rounded-full px-2.5 py-1"
            style={{ border: `1px solid ${colorForModel(name, index)}`, color: colorForModel(name, index), background: activeModel === name ? "rgba(90,138,90,0.10)" : "transparent" }}>
            {name}
          </button>
        ))}
        <span className="px-1 py-1">Use the list below for full details.</span>
      </div>
      <div ref={containerRef} className="relative rounded-2xl overflow-hidden" style={{ background: "var(--surface)", border: "1px solid var(--border-mid)" }}>
        <svg ref={svgRef} role="img" aria-label="Interactive reading universe map. Recommendation details are also listed below."
          className="block w-full" style={{ height: 430 }} />
        {tooltip && (
          <div className="absolute z-10 pointer-events-none rounded-xl p-3 text-xs shadow-lg" style={{
            left: tooltipLeft, top: clamp(tooltip.y - 56, 8, 360), maxWidth: 220,
            background: "var(--surface)", border: "1px solid var(--border-mid)",
          }}>
            <strong style={{ color: "var(--text-1)" }}>{tooltip.point.title}</strong>
            <div style={{ color: "var(--text-2)" }}>{tooltip.point.author}</div>
            {tooltip.type === "recommendation" && (
              <div className="mt-1" style={{ color: "var(--text-3)" }}>{tooltip.point.models.join(" + ")}</div>
            )}
          </div>
        )}
      </div>
      {(selectedBooks.length > 0 || nearby.length > 0) && (
        <div className="rounded-xl p-3 text-xs" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
          {selectedBooks.length > 0 && <p><strong>{selectedBooks.length} books in this cluster:</strong> {selectedBooks.slice(0, 5).map((book) => book.title).join(", ")}{selectedBooks.length > 5 ? "…" : ""}</p>}
          {nearby.length > 0 && <p><strong>Nearest picks for {selectedGenre?.name}:</strong> {nearby.map(({ recommendation }) => recommendation.title).join(", ")}</p>}
        </div>
      )}
    </div>
  );
}

function RecommendationList({
  recommendations,
  libbyData,
  library,
}: {
  recommendations: NormalizedRecommendation[];
  libbyData?: LibbyResponse | null;
  library?: string;
}) {
  return (
    <section aria-labelledby="recommendations-heading" className="space-y-3">
      <div>
        <h3 id="recommendations-heading" className="text-lg" style={{ fontFamily: "var(--font-dm-serif)", fontStyle: "italic", color: "var(--text-1)" }}>Recommendations</h3>
        <p className="text-xs" style={{ color: "var(--text-3)" }}>Each recommendation is available as text, independent of the visual map.</p>
      </div>
      {recommendations.length === 0 ? (
        <p className="text-sm rounded-xl p-4" style={{ background: "var(--surface)", color: "var(--text-2)", border: "1px solid var(--border)" }}>No valid recommendations were returned. Model errors are shown below.</p>
      ) : (
        <ul className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {recommendations.map((recommendation) => (
            <li key={recommendation.key} className="rounded-2xl p-4 space-y-2" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
              <div className="flex gap-3">
                <BookCover isbn={recommendation.isbn} title={recommendation.title} author={recommendation.author} size={44} />
                <div className="min-w-0">
                  <h4 className="font-medium text-sm" style={{ color: "var(--text-1)" }}>{recommendation.title}</h4>
                  <p className="text-xs" style={{ color: "var(--text-2)" }}>{recommendation.author ?? "Unknown author"}</p>
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5 text-[10px]">
                <span className="px-2 py-0.5 rounded-full" style={{ color: "var(--sage-dark)", background: "rgba(90,138,90,0.10)" }}>
                  {recommendation.isConsensus ? `Consensus: ${recommendation.models.join(" + ")}` : `Model: ${recommendation.models[0]}`}
                </span>
                {typeof recommendation.comfort_zone === "boolean" && (
                  <span className="px-2 py-0.5 rounded-full" style={{ background: "var(--surface-2)", color: "var(--text-3)" }}>
                    {recommendation.comfort_zone ? "Comfort-zone fit" : "Outside comfort zone"}
                  </span>
                )}
                {recommendation.on_tbr && <span className="px-2 py-0.5 rounded-full" style={{ color: "var(--sage-dark)", background: "rgba(90,138,90,0.10)" }}>On your TBR</span>}
                {recommendation.hidden_gem && <span className="px-2 py-0.5 rounded-full" style={{ color: "#8a6c20", background: "rgba(196,160,80,0.12)" }}>Hidden gem</span>}
                {library && <span className="px-2 py-0.5 rounded-full" style={{ color: "var(--text-3)", background: "var(--surface-2)" }}>{availabilityText(libbyData, recommendation.isbn)}</span>}
              </div>
              {Object.entries(recommendation.reasons).map(([model, reason]) => (
                <p key={model} className="text-xs leading-relaxed" style={{ color: "var(--text-2)" }}>
                  <strong>{model}:</strong> {reason}
                </p>
              ))}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function UnifiedMap({
  mapData,
  mapError,
  battle,
  libbyData,
  libbyError,
  library,
  judgeLoading,
  judgeError,
  onRunJudge,
  onRetryMap,
  onRetryLibby,
}: Props) {
  const modelEntries = useMemo(() => Object.entries(battle.models), [battle.models]);
  const modelNames = useMemo(() => modelEntries.map(([name]) => name), [modelEntries]);
  const recommendations = useMemo(() => normalizeRecommendations(battle.models), [battle.models]);
  const mapRecommendations = useMemo(() => mapData ? normalizeMapRecommendations(mapData.rec_points) : [], [mapData]);
  const successfulModels = modelEntries.filter(([, model]) => !model.error).length;
  const judge = battle.judge;
  const hasJudgeData = judge && Object.values(judge).some((verdict) => !verdict.error);

  return (
    <section className="space-y-5">
      <div className="flex items-baseline justify-between gap-4">
        <div>
          <h2 className="text-xl font-light tracking-tight" style={{ fontFamily: "var(--font-dm-serif)", color: "var(--text-1)", fontStyle: "italic" }}>Reading Universe</h2>
          <p className="text-xs mt-1" style={{ color: "var(--text-3)" }}>{successfulModels} of {modelEntries.length} models returned usable results.</p>
        </div>
      </div>

      <RecommendationList recommendations={recommendations} libbyData={libbyData} library={library} />

      {library && (
        <div role={libbyError ? "alert" : "status"} className="rounded-xl p-4 text-xs" style={{ background: "var(--surface)", border: "1px solid var(--border)" }}>
          {libbyError ? (
            <div className="flex justify-between gap-3"><span style={{ color: "var(--rust)" }}>Libby check failed: {libbyError}</span><button className="underline" onClick={onRetryLibby}>Retry Libby</button></div>
          ) : !libbyData ? (
            <span style={{ color: "var(--text-3)" }}>Checking availability at {library}…</span>
          ) : libbyData.skipped_reason === "no_isbns" ? (
            <span style={{ color: "var(--text-3)" }}>Availability could not be checked because the recommendations did not include verified ISBNs.</span>
          ) : libbyData.warnings?.length ? (
            <span style={{ color: "var(--text-3)" }}>{libbyData.warnings.join(" ")}</span>
          ) : !libbyData.library_found ? (
            <span style={{ color: "var(--rust)" }}>We could not match “{library}”. {libbyData.alternatives?.length ? `Try: ${libbyData.alternatives.join(", ")}.` : "Try a more specific library name."}</span>
          ) : (
            <span style={{ color: "var(--text-2)" }}>Libby availability checked at {libbyData.matched_library_name ?? libbyData.library_name ?? library}.</span>
          )}
        </div>
      )}

      <div>
        <h3 className="text-lg mb-2" style={{ fontFamily: "var(--font-dm-serif)", fontStyle: "italic", color: "var(--text-1)" }}>Map supplement</h3>
        {mapError ? (
          <div role="alert" className="rounded-xl p-4 text-sm flex justify-between gap-4" style={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--rust)" }}>
            <span>The reading map could not be generated: {mapError}</span>
            <button className="underline shrink-0" onClick={onRetryMap}>Retry map</button>
          </div>
        ) : mapData ? (
          <MapCanvas mapData={mapData} recommendations={mapRecommendations} modelNames={modelNames} />
        ) : (
          <div className="rounded-xl p-4 text-sm" style={{ background: "var(--surface)", color: "var(--text-3)", border: "1px solid var(--border)" }}>Generating your map in parallel with availability checks…</div>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {modelEntries.map(([name, model], index) => {
          const recommendationCount = model.recommendations.length;
          const tokensPerRecommendation = model.meta?.completion_tokens && recommendationCount
            ? Math.round(model.meta.completion_tokens / recommendationCount)
            : null;
          const tokensPerSecond = model.meta?.completion_tokens && model.meta.generation_ms
            ? Math.round(model.meta.completion_tokens / (model.meta.generation_ms / 1000))
            : null;
          const color = colorForModel(name, index);
          return (
            <article key={name} className="rounded-2xl p-5 space-y-3" style={{ background: "var(--surface)", borderLeft: `3px solid ${color}`, borderTop: "1px solid var(--border)", borderRight: "1px solid var(--border)", borderBottom: "1px solid var(--border)" }}>
              <div><h3 className="text-sm font-medium" style={{ color: "var(--text-1)" }}>{name}</h3><p className="text-xs mt-1" style={{ color: "var(--text-3)" }}>{model.info?.description}</p></div>
              {model.error ? <p role="alert" className="text-xs" style={{ color: "var(--rust)" }}>Model error: {model.error}</p> : (
                <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs" style={{ color: "var(--text-2)" }}>
                  <span>{recommendationCount} recommendation{recommendationCount === 1 ? "" : "s"}</span>
                  {tokensPerRecommendation !== null && <span>{tokensPerRecommendation} output tokens/recommendation</span>}
                  {tokensPerSecond !== null && <span>{tokensPerSecond.toLocaleString()} output tokens/sec</span>}
                  {model.meta?.ttft_ms != null && <span>{model.meta.ttft_ms.toLocaleString()} ms to first token</span>}
                </div>
              )}
              {model.meta?.ttft_ms != null && <p className="text-[10px]" style={{ color: "var(--text-3)" }}>TTFT is the measured delay before the first token; this measurement alone does not establish its cause.</p>}
            </article>
          );
        })}
      </div>

      <section className="rounded-2xl p-5 space-y-4" style={{ background: "var(--surface)", border: "1px solid var(--border-mid)" }}>
        <div className="flex justify-between gap-4 items-center">
          <div><h3 className="text-sm font-medium" style={{ color: "var(--text-1)" }}>Optional local judge</h3><p className="text-xs" style={{ color: "var(--text-3)" }}>A local model scores the returned recommendations. A tie or no winner is shown as such.</p></div>
          <button onClick={onRunJudge} disabled={judgeLoading || !onRunJudge} className="rounded-xl px-4 py-2 text-xs disabled:opacity-50" style={{ color: "var(--sage-dark)", border: "1px solid rgba(90,138,90,0.30)", background: "rgba(90,138,90,0.10)" }}>
            {judgeLoading ? "Judging…" : judgeError ? "Retry judge" : hasJudgeData ? "Run judge again" : "Run judge"}
          </button>
        </div>
        {judgeError && <p role="alert" className="text-xs" style={{ color: "var(--rust)" }}>Judge error: {judgeError}</p>}
        {hasJudgeData && judge && (
          <div className="space-y-3">
            {battle.winner ? (
              <p className="text-xs" style={{ color: "var(--text-2)" }}>Judge winner: <strong>{battle.winner}</strong></p>
            ) : (
              <p className="text-xs" style={{ color: "var(--text-3)" }}>{battle.tie ? "The judge scored this as a tie." : "The judge did not select a winner."}</p>
            )}
            <div className="grid md:grid-cols-2 gap-3">
              {modelNames.map((name) => {
                const verdict = judge[name];
                const entries = scoreEntries(verdict);
                return (
                  <div key={name} className="rounded-xl p-3 space-y-2" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                    <h4 className="text-xs font-medium" style={{ color: "var(--text-1)" }}>{name}</h4>
                    {verdict?.error ? <p className="text-xs" style={{ color: "var(--rust)" }}>{verdict.error}</p> : entries.length === 0 ? <p className="text-xs" style={{ color: "var(--text-3)" }}>No valid score returned.</p> : entries.map(([label, score]) => {
                      const safeScore = clamp(score, 0, 10);
                      return <div key={label}><div className="flex justify-between text-[10px]" style={{ color: "var(--text-3)" }}><span>{label}</span><span>{safeScore}/10</span></div><div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--border-mid)" }}><div className="h-full" style={{ width: `${safeScore * 10}%`, background: colorForModel(name) }} /></div></div>;
                    })}
                    {verdict?.verdict && <p className="text-xs leading-relaxed" style={{ color: "var(--text-2)" }}>{verdict.verdict}</p>}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </section>
    </section>
  );
}
