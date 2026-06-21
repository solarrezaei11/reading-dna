"use client";

// Pixel palette
const P: Record<string, string | null> = {
  '.': null,
  'C': '#fff0d8',  // warm cream body (distinct from bg)
  'D': '#7a4520',  // dark brown points
  'i': '#d49070',  // ear inner
  'B': '#89b8e8',  // soft blue eye
  'p': '#1a1422',  // pupil
  'w': '#ffffff',  // eye shine
  'N': '#e07888',  // pink nose
  'G': '#5a8a5a',  // book cover
  's': '#3d6b3d',  // spine
  'g': '#fdfaf0',  // page
  'd': '#2a1a12',  // closed-eye line
  'm': '#9a6858',  // mouth
};

// 16 wide × 22 tall
const OPEN = [
  '..DD........DD..',  //  0 — ear tips
  '.DiDD......DDiD.',  //  1
  '.DDCCCCCCCCCCDD.',  //  2 — head
  '.CCCCCCCCCCCCCC.',  //  3
  'CCCCCCCCCCCCCCCC',  //  4
  'CCCCCCCCCCCCCCCC',  //  5
  'CCCCBBCCCCBBCCCC',  //  6 — 2-wide soft eyes (less stare-y)
  'CCCCBpCNNCBpCCCC',  //  7 — pupils + nose
  'CCCCBwCCCCBwCCCC',  //  8 — eye shine
  'CCCCCCCmCmCCCCCC',  //  9 — cute ω mouth
  '.CCCCCCCCCCCCCC.',  // 10
  '..CCCCCCCCCCCC..',  // 11 — neck
  '.CCCCCCCCCCCCCC.',  // 12
  'CCCCCCCCCCCCCCCC',  // 13
  'CCCCCCCCCCCCCCCC',  // 14
  'CCCCCCCCCCCCCCCC',  // 15
  'DDCCCCCCCCCCCCDD',  // 16 — paw tops
  'DD..GGGGsgggggDD',  // 17 — paws + book
  '....GGGGsggggg..',  // 18
  '....GGGGsggggg..',  // 19
  '....GGGGsggggg..',  // 20
  '....GGGGsggggg..',  // 21
];

const CLOSED_6 = 'CCCCCCCCCCCCCCCC';
const CLOSED_7 = 'CCCCddCCCCddCCCC';  // 2-wide blink line
const CLOSED_8 = 'CCCCCCCCCCCCCCCC';

const SZ = 8;
const W  = 16 * SZ;
const H  = 22 * SZ;

function makeRects(grid: string[], className?: string) {
  return grid.flatMap((row, gy) =>
    [...row].flatMap((ch, gx) => {
      const fill = P[ch];
      if (!fill) return [];
      return [(
        <rect
          key={`${className ?? 'b'}-${gx}-${gy}`}
          x={gx * SZ} y={gy * SZ}
          width={SZ} height={SZ}
          fill={fill}
          shapeRendering="crispEdges"
        />
      )];
    })
  );
}

export default function HimalayanCat() {
  const bodyRows = OPEN.map((row, i) =>
    i >= 6 && i <= 8 ? '.'.repeat(16) : row
  );
  const eyeOpenRows = OPEN.map((row, i) =>
    i >= 6 && i <= 8 ? row : '.'.repeat(16)
  );
  const eyeClosedRows = OPEN.map((_row, i) =>
    i === 6 ? CLOSED_6 : i === 7 ? CLOSED_7 : i === 8 ? CLOSED_8 : '.'.repeat(16)
  );

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width={W}
      height={H}
      style={{ imageRendering: "pixelated" }}
      aria-label="Himalayan cat reading a book"
    >
      <defs>
        <filter id="hcp-shadow" x="-25%" y="-15%" width="150%" height="140%">
          <feDropShadow dx="0" dy="4" stdDeviation="5" floodColor="#8b6b46" floodOpacity="0.18" />
        </filter>
        <style>{`
          @keyframes hcp-float {
            0%,100% { transform: translateY(0px); }
            50%      { transform: translateY(-${SZ}px); }
          }
          @keyframes hcp-blink-open {
            0%,85%,100% { opacity: 1; }
            90%,95%     { opacity: 0; }
          }
          @keyframes hcp-blink-closed {
            0%,85%,100% { opacity: 0; }
            90%,95%     { opacity: 1; }
          }
          @keyframes hcp-page {
            0%,70%,100% { opacity: 1; }
            80%,90%     { opacity: 0.35; }
          }
          .hcp-float        { animation: hcp-float  3.5s ease-in-out infinite; }
          .hcp-eyes-open    { animation: hcp-blink-open   5s ease-in-out infinite; }
          .hcp-eyes-closed  { animation: hcp-blink-closed 5s ease-in-out infinite; }
          .hcp-page         { animation: hcp-page   6s ease-in-out infinite; }
        `}</style>
      </defs>

      <g className="hcp-float" filter="url(#hcp-shadow)">
        {/* Body, ears, paws, mouth (no eye rows 6-8) */}
        {makeRects(bodyRows, 'body')}

        {/* Eyes open */}
        <g className="hcp-eyes-open">
          {makeRects(eyeOpenRows, 'eo')}
        </g>

        {/* Eyes closed blink */}
        <g className="hcp-eyes-closed">
          {makeRects(eyeClosedRows, 'ec')}
        </g>

        {/* Whiskers */}
        <line x1={0}   y1={7.2 * SZ} x2={3.2 * SZ} y2={7 * SZ}   stroke="#c0a888" strokeWidth="1" />
        <line x1={0}   y1={8.4 * SZ} x2={3.2 * SZ} y2={8 * SZ}   stroke="#c0a888" strokeWidth="1" />
        <line x1={W}   y1={7.2 * SZ} x2={12.8 * SZ} y2={7 * SZ}  stroke="#c0a888" strokeWidth="1" />
        <line x1={W}   y1={8.4 * SZ} x2={12.8 * SZ} y2={8 * SZ}  stroke="#c0a888" strokeWidth="1" />

        {/* Page shimmer */}
        <g className="hcp-page">
          <rect
            x={9 * SZ} y={17 * SZ}
            width={5 * SZ} height={5 * SZ}
            fill="rgba(253,250,240,0.6)"
            shapeRendering="crispEdges"
          />
        </g>
      </g>
    </svg>
  );
}
