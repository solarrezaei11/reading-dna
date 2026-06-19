"use client";

type Props = { dna: any };

const DIM_LABELS: Record<string, [string, string]> = {
  prose_density: ["Breezy", "Dense"],
  pacing_preference: ["Slow burn", "Fast-paced"],
  intellectual_depth: ["Light", "Deep"],
  emotional_intensity: ["Cool", "Intense"],
  contrarian_score: ["Goes with crowd", "Contrarian"],
};


export default function DNAProfile({ dna }: Props) {
  const dims = dna.taste_dimensions || {};

  return (
    <section className="space-y-6">
      <h2 className="text-lg font-semibold text-zinc-300">Your Reading DNA</h2>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

        {/* Archetype card */}
        <div className="bg-white/5 border border-white/10 rounded-2xl p-6 space-y-2 md:col-span-2">
          <div className="text-xs text-purple-400 font-mono tracking-widest uppercase">Archetype</div>
          <div className="text-2xl font-bold">{dna.reader_archetype}</div>
          <p className="text-zinc-400 text-sm leading-relaxed">{dna.taste_summary}</p>
        </div>

        {/* Taste dimensions */}
        <div className="bg-white/5 border border-white/10 rounded-2xl p-5 space-y-4">
          <div className="text-xs text-zinc-400 font-mono tracking-widest uppercase">Taste Dimensions</div>
          {Object.entries(DIM_LABELS).map(([key, [lo, hi]]) => {
            const val = dims[key] ?? 5;
            return (
              <div key={key} className="space-y-1">
                <div className="flex justify-between text-xs text-zinc-500">
                  <span>{lo}</span>
                  <span>{hi}</span>
                </div>
                <div className="h-1.5 bg-white/10 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-purple-500 rounded-full transition-all duration-700"
                    style={{ width: `${val * 10}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>

        {/* Themes */}
        <div className="bg-white/5 border border-white/10 rounded-2xl p-5 space-y-4">
          <div className="text-xs text-zinc-400 font-mono tracking-widest uppercase">Top Themes</div>
          <div className="flex flex-wrap gap-2">
            {(dna.top_themes || []).map((t: string) => (
              <span key={t} className="text-xs bg-purple-500/20 text-purple-300 border border-purple-500/30 px-3 py-1 rounded-full">
                {t}
              </span>
            ))}
          </div>
          {dna.avoid_themes?.length > 0 && (
            <>
              <div className="text-xs text-zinc-400 font-mono tracking-widest uppercase mt-3">Tends to Avoid</div>
              <div className="flex flex-wrap gap-2">
                {dna.avoid_themes.map((t: string) => (
                  <span key={t} className="text-xs bg-red-500/10 text-red-400 border border-red-500/20 px-3 py-1 rounded-full">
                    {t}
                  </span>
                ))}
              </div>
            </>
          )}

          {dna.blind_spot_genres?.length > 0 && (
            <>
              <div className="text-xs text-zinc-400 font-mono tracking-widest uppercase mt-3">Blind Spots</div>
              <div className="flex flex-wrap gap-2">
                {dna.blind_spot_genres.map((t: string) => (
                  <span key={t} className="text-xs bg-yellow-500/10 text-yellow-400 border border-yellow-500/20 px-3 py-1 rounded-full">
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
