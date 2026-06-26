"use client";

const READERS = [
  {
    name: "Barack Obama",
    role: "44th U.S. President",
    emoji: "🏛️",
    dims: { prose_density: 7, pacing_preference: 4, intellectual_depth: 9, emotional_intensity: 6, contrarian_score: 5, fiction_ratio: 55 },
    themes: ["democracy", "race", "identity", "justice", "history", "power", "class struggle", "social injustice"],
    note: "Like Obama, you're drawn to books that carry both literary weight and moral seriousness — stories that explore power, identity, and what it means to belong to something larger than yourself.",
  },
  {
    name: "Oprah Winfrey",
    role: "Media icon & book club legend",
    emoji: "✨",
    dims: { prose_density: 4, pacing_preference: 6, intellectual_depth: 7, emotional_intensity: 9, contrarian_score: 2, fiction_ratio: 65 },
    themes: ["resilience", "identity", "healing", "women", "self-discovery", "trauma", "spirituality", "emotional growth"],
    note: "Like Oprah, you read with your whole heart. You're drawn to books that crack you open — ones that center powerful women and leave you fundamentally changed.",
  },
  {
    name: "Bill Gates",
    role: "Tech founder & prolific nonfiction reader",
    emoji: "💡",
    dims: { prose_density: 5, pacing_preference: 7, intellectual_depth: 9, emotional_intensity: 3, contrarian_score: 5, fiction_ratio: 15 },
    themes: ["technology", "global health", "innovation", "economics", "science", "climate", "systems thinking", "history of science"],
    note: "Like Gates, you read to understand how the world actually works. You want frameworks, not feelings — books that give you a sharper mental model of complex systems.",
  },
  {
    name: "Reese Witherspoon",
    role: "Actress & Hello Sunshine founder",
    emoji: "🌟",
    dims: { prose_density: 3, pacing_preference: 8, intellectual_depth: 5, emotional_intensity: 7, contrarian_score: 2, fiction_ratio: 85 },
    themes: ["women", "friendship", "family", "romance", "female agency", "Southern life", "coming of age", "community"],
    note: "Like Reese, you love books that move fast, center women, and make you want to call a friend the moment you finish. You value momentum and emotional warmth over literary density.",
  },
  {
    name: "Susan Sontag",
    role: "Critic, essayist & intellectual icon",
    emoji: "🖋️",
    dims: { prose_density: 10, pacing_preference: 2, intellectual_depth: 10, emotional_intensity: 5, contrarian_score: 9, fiction_ratio: 45 },
    themes: ["art", "photography", "illness", "war", "criticism", "philosophy", "aesthetics", "political theory"],
    note: "Like Sontag, you read at the edge of what's comfortable. Dense prose is a feature, not a bug — you want books that fundamentally reframe how you see everything.",
  },
  {
    name: "Taylor Swift",
    role: "Artist & literary fiction devotee",
    emoji: "🎵",
    dims: { prose_density: 4, pacing_preference: 6, intellectual_depth: 6, emotional_intensity: 10, contrarian_score: 4, fiction_ratio: 70 },
    themes: ["love", "heartbreak", "female experience", "nostalgia", "identity", "betrayal", "storytelling", "psychological manipulation"],
    note: "Like Taylor, you feel every line. You're a collector of sentences that hit exactly right — books that turn private, overwhelming feelings into something universal and survivable.",
  },
  {
    name: "Stephen King",
    role: "Master of horror & constant reader",
    emoji: "🔦",
    dims: { prose_density: 4, pacing_preference: 9, intellectual_depth: 5, emotional_intensity: 9, contrarian_score: 3, fiction_ratio: 90 },
    themes: ["fear", "supernatural", "childhood", "evil", "survival", "psychological horror", "small-town life", "addiction"],
    note: "Like King, you want to be pulled under. Pacing is everything, intensity is the point — and you have a high tolerance for going to dark places if the story earns it.",
  },
  {
    name: "Malala Yousafzai",
    role: "Nobel laureate & education activist",
    emoji: "📚",
    dims: { prose_density: 5, pacing_preference: 6, intellectual_depth: 8, emotional_intensity: 7, contrarian_score: 7, fiction_ratio: 30 },
    themes: ["education", "women's rights", "social justice", "courage", "identity", "activism", "political oppression", "historical oppression"],
    note: "Like Malala, you read with purpose. Books are a way to understand injustice and imagine better worlds — and you're not afraid to choose something that challenges the dominant narrative.",
  },
  {
    name: "Neil deGrasse Tyson",
    role: "Astrophysicist & science communicator",
    emoji: "🔭",
    dims: { prose_density: 4, pacing_preference: 7, intellectual_depth: 9, emotional_intensity: 4, contrarian_score: 6, fiction_ratio: 20 },
    themes: ["science", "cosmos", "physics", "wonder", "skepticism", "evolution", "space", "popular science"],
    note: "Like Tyson, you read to be astonished. The universe is the best story ever told, and you want books that zoom out to reveal the jaw-dropping scale of reality.",
  },
  {
    name: "Emma Watson",
    role: "Actress & feminist book club founder",
    emoji: "📖",
    dims: { prose_density: 6, pacing_preference: 5, intellectual_depth: 8, emotional_intensity: 7, contrarian_score: 6, fiction_ratio: 60 },
    themes: ["feminism", "female agency", "social justice", "identity", "mental health", "women", "intersectionality", "class struggle"],
    note: "Like Emma, you read with intention. You want books that are both beautifully written and morally alive — stories that expand your empathy and sharpen your worldview.",
  },
];

function score(dna: any, reader: (typeof READERS)[0]): number {
  const d = dna.taste_dimensions || {};
  const u = [
    (d.prose_density     ?? 5) / 10,
    (d.pacing_preference ?? 5) / 10,
    (d.intellectual_depth?? 5) / 10,
    (d.emotional_intensity??5) / 10,
    (d.contrarian_score  ?? 5) / 10,
    (d.fiction_ratio     ??50) / 100,
  ];
  const r = [
    reader.dims.prose_density / 10,
    reader.dims.pacing_preference / 10,
    reader.dims.intellectual_depth / 10,
    reader.dims.emotional_intensity / 10,
    reader.dims.contrarian_score / 10,
    reader.dims.fiction_ratio / 100,
  ];
  const dist = Math.sqrt(u.reduce((s, v, i) => s + (v - r[i]) ** 2, 0));
  const userThemes = (dna.top_themes || []).map((t: string) => t.toLowerCase());
  const overlap = reader.themes.filter(t =>
    userThemes.some((ut: string) => ut.includes(t) || t.includes(ut))
  ).length;
  const raw = (1 - dist / Math.sqrt(6)) + overlap * 0.04;
  return Math.round(Math.min(raw, 1) * 100);
}

export default function FamousReaderMatch({ dna }: { dna: any }) {
  const ranked = [...READERS].map(r => ({ ...r, pct: score(dna, r) })).sort((a, b) => b.pct - a.pct);
  const top = ranked[0];
  const runner = ranked[1];

  return (
    <div
      className="rounded-2xl p-6 space-y-4"
      style={{
        background: "linear-gradient(135deg, #1a200f 0%, #141a0c 100%)",
        border: "1px solid rgba(90,138,90,0.2)",
        boxShadow: "0 4px 20px rgba(0,0,0,0.12)",
      }}
    >
      <div className="text-[10px] tracking-[0.2em] uppercase" style={{ color: "rgba(90,138,90,0.65)", fontFamily: "var(--font-geist-mono)" }}>
        Your reading DNA matches
      </div>

      <div className="flex items-start gap-4">
        <div className="text-4xl leading-none mt-1 select-none">{top.emoji}</div>
        <div className="flex-1 min-w-0">
          <div
            className="text-2xl leading-tight"
            style={{ fontFamily: "var(--font-dm-serif)", color: "#f2ece0", fontStyle: "italic" }}
          >
            {top.name}
          </div>
          <div className="text-[11px] mt-0.5" style={{ color: "rgba(160,136,112,0.65)" }}>{top.role}</div>
          <p className="mt-2.5 text-xs leading-relaxed" style={{ color: "rgba(200,185,160,0.8)" }}>
            {top.note}
          </p>
        </div>
        <div className="text-right shrink-0">
          <div className="text-2xl font-semibold tabular-nums" style={{ color: "#7ab87a" }}>{top.pct}%</div>
          <div className="text-[9px] uppercase tracking-widest mt-0.5" style={{ color: "rgba(90,138,90,0.45)" }}>match</div>
        </div>
      </div>

      <div
        className="pt-3 border-t flex items-center gap-3"
        style={{ borderColor: "rgba(90,138,90,0.12)" }}
      >
        <span className="text-base leading-none select-none">{runner.emoji}</span>
        <div className="flex-1 text-xs" style={{ color: "rgba(200,185,160,0.5)" }}>
          Runner-up: <span style={{ color: "rgba(200,185,160,0.75)" }}>{runner.name}</span>
        </div>
        <div className="text-xs tabular-nums" style={{ color: "rgba(90,138,90,0.5)" }}>{runner.pct}%</div>
      </div>
    </div>
  );
}
