"use client";

type Props = { dna: any };

const DIM_LABELS: Record<string, [string, string]> = {
  prose_density:      ["Breezy prose",  "Dense prose"],
  pacing_preference:  ["Slow burn",     "Fast-paced"],
  intellectual_depth: ["Light",         "Deep"],
  emotional_intensity:["Cool",          "Intense"],
  contrarian_score:   ["Crowd-pleaser", "Contrarian"],
};

export default function DNAProfile({ dna }: Props) {
  const dims = dna.taste_dimensions || {};

  return (
    <section className="space-y-5">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

        {/* Archetype */}
        <div
          className="rounded-2xl p-6 space-y-3 md:col-span-2"
          style={{ background: "var(--surface)", border: "1px solid var(--border-mid)", boxShadow: "0 2px 12px rgba(139,107,70,0.06)" }}
        >
          <div className="text-[10px] tracking-[0.18em] uppercase font-medium" style={{ color: "var(--sage-dark)" }}>
            Your Archetype
          </div>
          <div className="text-3xl leading-tight" style={{ fontFamily: "var(--font-dm-serif)", color: "var(--text-1)", fontStyle: "italic" }}>
            {dna.reader_archetype}
          </div>
          <p className="text-sm leading-relaxed" style={{ color: "var(--text-2)" }}>{dna.taste_summary}</p>
        </div>

        {/* Taste Spectrum */}
        <div
          className="rounded-2xl p-5 space-y-4"
          style={{ background: "var(--surface)", border: "1px solid var(--border)", boxShadow: "0 1px 6px rgba(139,107,70,0.04)" }}
        >
          <div className="text-[10px] tracking-[0.18em] uppercase font-medium" style={{ color: "var(--text-3)" }}>
            Taste Spectrum
          </div>
          {Object.entries(DIM_LABELS).map(([key, [lo, hi]]) => {
            const val = dims[key] ?? 5;
            return (
              <div key={key} className="space-y-1.5">
                <div className="flex justify-between text-[11px]" style={{ color: "var(--text-3)" }}>
                  <span>{lo}</span><span>{hi}</span>
                </div>
                <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--border-mid)" }}>
                  <div className="h-full rounded-full transition-all duration-700" style={{ width: `${val * 10}%`, background: "var(--sage)" }} />
                </div>
              </div>
            );
          })}
        </div>

        {/* Themes */}
        <div
          className="rounded-2xl p-5 space-y-4"
          style={{ background: "var(--surface)", border: "1px solid var(--border)", boxShadow: "0 1px 6px rgba(139,107,70,0.04)" }}
        >
          <div className="text-[10px] tracking-[0.18em] uppercase font-medium" style={{ color: "var(--text-3)" }}>Top Themes</div>
          <div className="flex flex-wrap gap-2">
            {(dna.top_themes || []).map((t: string) => (
              <span key={t} className="text-xs px-3 py-1 rounded-full"
                style={{ color: "var(--sage-dark)", background: "rgba(90,138,90,0.10)", border: "1px solid rgba(90,138,90,0.25)" }}>
                {t}
              </span>
            ))}
          </div>

          {dna.avoid_themes?.length > 0 && (
            <>
              <div className="text-[10px] tracking-[0.18em] uppercase font-medium" style={{ color: "var(--text-3)" }}>Tends to Avoid</div>
              <div className="flex flex-wrap gap-2">
                {dna.avoid_themes.map((t: string) => (
                  <span key={t} className="text-xs px-3 py-1 rounded-full"
                    style={{ color: "var(--rust)", background: "rgba(176,90,69,0.08)", border: "1px solid rgba(176,90,69,0.2)" }}>
                    {t}
                  </span>
                ))}
              </div>
            </>
          )}

          {dna.blind_spot_genres?.length > 0 && (
            <>
              <div className="text-[10px] tracking-[0.18em] uppercase font-medium" style={{ color: "var(--text-3)" }}>Blind Spots</div>
              <div className="flex flex-wrap gap-2">
                {dna.blind_spot_genres.map((t: string) => (
                  <span key={t} className="text-xs px-3 py-1 rounded-full"
                    style={{ color: "var(--sand)", background: "rgba(155,120,85,0.08)", border: "1px solid rgba(155,120,85,0.22)" }}>
                    {t}
                  </span>
                ))}
              </div>
            </>
          )}
        </div>

      </div>
    </section>
  );
}
