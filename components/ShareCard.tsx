"use client";

import { useRef } from "react";

type Props = {
  dna: any;
  bookCount: number;
};

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

  return (
    <section className="space-y-4">

      {/* The card */}
      <div
        ref={cardRef}
        className="relative overflow-hidden rounded-3xl max-w-sm w-full"
        style={{ background: "linear-gradient(135deg, #1a0533 0%, #0d0d2b 40%, #0a0a1f 100%)" }}
      >
        {/* Background glow blobs */}
        <div className="absolute top-0 right-0 w-64 h-64 rounded-full opacity-20 blur-3xl"
          style={{ background: "radial-gradient(circle, #a855f7, transparent)" }} />
        <div className="absolute bottom-0 left-0 w-48 h-48 rounded-full opacity-15 blur-3xl"
          style={{ background: "radial-gradient(circle, #6366f1, transparent)" }} />

        <div className="relative p-7 space-y-6">

          {/* Branding */}
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-mono tracking-[0.2em] text-purple-400 uppercase">ReadingDNA</span>
            <span className="text-[10px] text-zinc-600">2025</span>
          </div>

          {/* Archetype — the hero */}
          <div className="space-y-2">
            <div className="text-[10px] font-mono tracking-widest text-purple-400/70 uppercase">Your archetype</div>
            <div className="text-3xl font-bold text-white leading-tight tracking-tight">
              {dna.reader_archetype}
            </div>
            <div className="text-sm text-zinc-400 leading-relaxed">{dna.one_liner}</div>
          </div>

          {/* Divider */}
          <div className="h-px bg-white/10" />

          {/* Stats */}
          <div className="grid grid-cols-3 gap-3 text-center">
            <div>
              <div className="text-2xl font-bold text-white">{bookCount}</div>
              <div className="text-[10px] text-zinc-500 uppercase tracking-wider mt-0.5">Books</div>
            </div>
            <div>
              <div className="text-2xl font-bold text-white">{dna.avg_rating?.toFixed(1)}</div>
              <div className="text-[10px] text-zinc-500 uppercase tracking-wider mt-0.5">Avg rating</div>
            </div>
            <div>
              <div className="text-2xl font-bold text-white">{Math.round(dims.fiction_ratio ?? 50)}%</div>
              <div className="text-[10px] text-zinc-500 uppercase tracking-wider mt-0.5">Fiction</div>
            </div>
          </div>

          {/* Top themes */}
          <div className="space-y-2">
            <div className="text-[10px] font-mono tracking-widest text-purple-400/70 uppercase">Top themes</div>
            <div className="flex flex-wrap gap-1.5">
              {(dna.top_themes || []).slice(0, 5).map((t: string) => (
                <span key={t} className="text-xs text-purple-300 bg-purple-500/15 border border-purple-500/25 px-2.5 py-0.5 rounded-full">
                  {t}
                </span>
              ))}
            </div>
          </div>

          {/* Top books */}
          {topBooks.length > 0 && (
            <div className="space-y-2">
              <div className="text-[10px] font-mono tracking-widest text-purple-400/70 uppercase">Most loved</div>
              <div className="space-y-1.5">
                {topBooks.map((b: any, i: number) => (
                  <div key={i} className="flex items-baseline gap-2">
                    <span className="text-[10px] text-purple-500 font-mono">{i + 1}</span>
                    <span className="text-xs text-zinc-300 truncate">{b.title}</span>
                    <span className="text-[10px] text-zinc-600 shrink-0">— {b.author}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Taste line */}
          <div className="flex gap-2 text-[10px] text-zinc-600">
            {dims.prose_density > 6 ? <span>Dense prose</span> : dims.prose_density < 4 ? <span>Breezy reads</span> : <span>Balanced prose</span>}
            <span>·</span>
            {dims.pacing_preference > 6 ? <span>Fast-paced</span> : dims.pacing_preference < 4 ? <span>Slow burn</span> : <span>Varied pacing</span>}
            <span>·</span>
            <span>Depth {dims.intellectual_depth}/10</span>
          </div>

        </div>
      </div>

      {/* Copy button */}
      <button
        onClick={copyToClipboard}
        className="flex items-center gap-2 px-4 py-2 bg-white/5 hover:bg-white/10 border border-white/10 rounded-xl text-sm text-zinc-300 transition-colors"
      >
        <span>Copy as image</span>
      </button>

    </section>
  );
}
