"use client";

import { useRef } from "react";

type Props = { dna: any; bookCount: number };

export default function ShareCard({ dna, bookCount }: Props) {
  const cardRef = useRef<HTMLDivElement>(null);

  const copyToClipboard = async () => {
    if (!cardRef.current) return;
    try {
      const { default: html2canvas } = await import("html2canvas");
      const canvas = await html2canvas(cardRef.current, { scale: 2, useCORS: true });
      canvas.toBlob((blob) => {
        if (!blob) return;
        navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      });
    } catch {
      alert("Screenshot the card to share!");
    }
  };

  const dims = dna.taste_dimensions || {};
  const topBooks = (dna.top_books || []).slice(0, 3);
  const paceLabel = dims.pacing_preference > 6 ? "Fast-paced" : dims.pacing_preference < 4 ? "Slow burn" : "Varied pacing";
  const proseLabel = dims.prose_density > 6 ? "Dense prose" : dims.prose_density < 4 ? "Breezy" : "Balanced prose";

  return (
    <section className="space-y-3">
      {/* The shareable card — kept dark/botanical for contrast when shared */}
      <div
        ref={cardRef}
        className="relative overflow-hidden rounded-3xl max-w-xs w-full"
        style={{ background: "linear-gradient(160deg, #1a200f 0%, #141a0c 55%, #0f1409 100%)" }}
      >
        {/* Botanical glows */}
        <div className="absolute top-0 right-0 w-56 h-56 rounded-full pointer-events-none"
          style={{ background: "radial-gradient(circle, rgba(90,138,90,0.15), transparent 70%)", transform: "translate(30%,-30%)" }} />
        <div className="absolute bottom-0 left-0 w-40 h-40 rounded-full pointer-events-none"
          style={{ background: "radial-gradient(circle, rgba(74,140,126,0.10), transparent 70%)", transform: "translate(-30%,30%)" }} />

        <div className="relative p-7 space-y-5">
          {/* Branding */}
          <div className="flex items-center justify-between">
            <span className="text-[10px] tracking-[0.2em] uppercase" style={{ color: "rgba(90,138,90,0.75)", fontFamily: "var(--font-geist-mono)" }}>
              ReadingDNA
            </span>
            <span className="text-[10px]" style={{ color: "rgba(160,136,112,0.6)" }}>2025</span>
          </div>

          {/* Archetype */}
          <div className="space-y-1.5">
            <div className="text-[10px] tracking-[0.18em] uppercase" style={{ color: "rgba(90,138,90,0.55)", fontFamily: "var(--font-geist-mono)" }}>
              Your archetype
            </div>
            <div className="text-[1.65rem] leading-tight" style={{ fontFamily: "var(--font-dm-serif)", color: "#f2ece0", fontStyle: "italic" }}>
              {dna.reader_archetype}
            </div>
            <div className="text-xs leading-relaxed" style={{ color: "rgba(200,185,160,0.85)" }}>{dna.one_liner}</div>
          </div>

          <div className="h-px" style={{ background: "rgba(139,107,70,0.2)" }} />

          {/* Stats */}
          <div className="grid grid-cols-3 gap-3 text-center">
            {[{ val: bookCount, label: "Books" }, { val: dna.avg_rating?.toFixed(1), label: "Avg rating" }, { val: `${Math.round(dims.fiction_ratio ?? 50)}%`, label: "Fiction" }].map(({ val, label }) => (
              <div key={label}>
                <div className="text-xl font-semibold" style={{ color: "#f2ece0" }}>{val}</div>
                <div className="text-[9px] tracking-wider uppercase mt-0.5" style={{ color: "rgba(160,136,112,0.7)" }}>{label}</div>
              </div>
            ))}
          </div>

          {/* Top themes */}
          <div className="space-y-2">
            <div className="text-[10px] tracking-[0.18em] uppercase" style={{ color: "rgba(90,138,90,0.55)", fontFamily: "var(--font-geist-mono)" }}>Top themes</div>
            <div className="flex flex-wrap gap-1.5">
              {(dna.top_themes || []).slice(0, 5).map((t: string) => (
                <span key={t} className="text-[11px] px-2.5 py-0.5 rounded-full"
                  style={{ color: "rgba(90,138,90,0.9)", background: "rgba(90,138,90,0.12)", border: "1px solid rgba(90,138,90,0.25)" }}>
                  {t}
                </span>
              ))}
            </div>
          </div>

          {/* Top books */}
          {topBooks.length > 0 && (
            <div className="space-y-2">
              <div className="text-[10px] tracking-[0.18em] uppercase" style={{ color: "rgba(90,138,90,0.55)", fontFamily: "var(--font-geist-mono)" }}>Most loved</div>
              <div className="space-y-1.5">
                {topBooks.map((b: any, i: number) => (
                  <div key={i} className="flex items-baseline gap-2">
                    <span className="text-[10px] font-mono" style={{ color: "rgba(90,138,90,0.5)" }}>{i + 1}</span>
                    <span className="text-xs truncate" style={{ color: "rgba(242,236,224,0.85)" }}>{b.title}</span>
                    <span className="text-[10px] shrink-0" style={{ color: "rgba(160,136,112,0.7)" }}>— {b.author}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Footnote */}
          <div className="flex flex-wrap gap-x-2 text-[10px]" style={{ color: "rgba(160,136,112,0.6)" }}>
            <span>{proseLabel}</span><span>·</span><span>{paceLabel}</span><span>·</span><span>Depth {dims.intellectual_depth}/10</span>
          </div>
        </div>
      </div>

      <button
        onClick={copyToClipboard}
        className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm transition-all"
        style={{ background: "var(--surface)", border: "1px solid var(--border-mid)", color: "var(--text-2)", boxShadow: "0 1px 4px rgba(139,107,70,0.06)" }}
        onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--sage)")}
        onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--border-mid)")}
      >
        Copy as image
      </button>
    </section>
  );
}
