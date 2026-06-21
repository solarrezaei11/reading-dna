# ReadingDNA 🌿

**A live AI model evaluation — applied to your reading taste.**

Paste your Goodreads profile. ReadingDNA builds a semantic taste profile from your reading history, then runs two frontier models head-to-head to recommend your next book. You see exactly which model knows you better, how fast each one responded, and where your recommendations land in the space of all books you've ever read.

Built to explore a question I work with daily as a TPM on AI evaluation at Microsoft: *given the same context, how differently do two models reason — and how do you measure that?*

---

<p align="center">
  <img src="public/screenshots/01_landing.png" alt="Landing page" width="49%" />
  <img src="public/screenshots/02_dna_profile.png" alt="Reading DNA profile" width="49%" />
</p>
<p align="center">
  <img src="public/screenshots/03_map.png" alt="Reading Universe Map with AI recommendations" width="98%" />
</p>

*Demo: [Emily May](https://www.goodreads.com/user/show/4622890-emily-may) — Goodreads' most-followed reviewer, 197 books. Archetype: **The Darkly Curious Intellectual**. GPT-OSS 120B responded in **1,047ms**.*

---

## Why Cerebras

A 120-billion-parameter model typically takes several seconds to respond — GPU infrastructure has to move enormous amounts of weight across memory on every token. Cerebras' wafer-scale chip is built differently: the entire model fits on a single chip, eliminating the memory bottleneck.

The result: **GPT-OSS 120B responds in ~1 second.** Same model you'd run on a GPU cluster and wait for — instant on Cerebras.

| Model | Parameters | TTFT | Generation | Total |
|---|---|---|---|---|
| GPT-OSS 120B (Cerebras) | 120B dense | **207 ms** | 1,140 ms | 1,347 ms |
| GLM 4.7 (Cerebras) | 355B MoE (32B active) | **206 ms** | 11,553 ms | 11,759 ms |

Both models start responding in ~200ms — same Cerebras infrastructure, same network. The difference is generation speed: GPT-OSS 120B, a fully dense model, completes in 1.1 seconds. GLM 4.7, despite having only 32B active parameters per token (MoE), takes 11.5 seconds to generate. Cerebras' wafer-scale chip is specifically optimized for dense transformer workloads — that's where the 10× generation speedup comes from.

---

## How it works

1. **Import** — paste your Goodreads profile URL. The backend fetches your read, currently-reading, and did-not-finish shelves via RSS (including your written reviews — sequential requests to avoid rate limiting). CSV export is also supported.

2. **Embed + cluster** — each book is converted to a 384-dim embedding via `all-MiniLM-L6-v2` (runs locally, no API cost), then reduced to 2D with UMAP alongside 15 fixed genre anchors. KMeans clusters your books; GPT-OSS 120B names each cluster at `temperature=0` for deterministic, stable labels.

3. **Model battle** — GPT-OSS 120B and GLM 4.7 each independently receive your full reading profile (titles, authors, ratings, review text, themes, archetype) and return 5 recommendations with reasoning. Consensus picks — titles both models chose — are highlighted. Picks outside your reading clusters get a "comfort zone" marker on the map.

4. **Visualize** — a D3.js map plots your book clusters, genre territory anchors, and AI picks as diamonds. Click any cluster to expand its books; click any genre anchor to surface the nearest AI picks. Click a model card to highlight only its picks on the map.

5. **Share** — export a shareable card: archetype, avg rating, top themes, most-loved books.

6. **Optional judge** — run Qwen 2.5 7B locally via Ollama as a neutral third-party judge. It evaluates each model's recommendations on four axes (relevance, reasoning depth, novelty, specificity) using the MT-Bench cross-evaluation approach: each model judges the other's output to avoid self-serving bias.

7. **Library check** — OverDrive/Libby availability for every recommendation at your local library.

---

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 14 (App Router) · TypeScript · Tailwind CSS · D3.js |
| Backend | FastAPI · Python · uvicorn |
| Embeddings | `sentence-transformers` — `all-MiniLM-L6-v2` (local, no API cost) |
| Dimensionality reduction | UMAP · KMeans (`scikit-learn`) |
| LLMs | Cerebras Cloud SDK — `gpt-oss-120b` · `zai-glm-4.7` |
| LLM judge (optional) | Qwen 2.5 7B via Ollama (local) |
| Export | `html2canvas` (share card PNG) |

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/solarrezaei11/reading-dna.git
cd reading-dna
npm install
```

### 2. Python backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Environment variables

```bash
cp .env.example .env.local
```

Edit `.env.local` and add your [Cerebras API key](https://cloud.cerebras.ai) (free tier available).

### 4. Run

```bash
# Terminal 1 — backend
cd backend && bash start.sh   # sources .env.local, then starts uvicorn

# Terminal 2 — frontend
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

### Goodreads profile setup

Your profile must be **public**: Goodreads → Account → Settings → Privacy → "Who can view my profile" → Everyone.

Use your full profile URL: `https://www.goodreads.com/user/show/12345678-your-name`

### Optional: LLM judge (Qwen via Ollama)

```bash
brew install ollama
ollama pull qwen2.5:7b
ollama serve
```

The "Run Judge" button appears on the results page. Takes ~2 minutes on local hardware.

---

## Design decisions

**Why `temperature=0` everywhere?** Early versions used `temperature=0.7–0.8`, which caused the archetype label, recommendations, and share card to shift between runs for the same library. Setting it to 0 makes the analysis fully deterministic — same Goodreads history always produces the same output.

**Why local embeddings?** Sending 200 book titles to an embedding API on every load adds latency, cost, and a network dependency. `all-MiniLM-L6-v2` runs in ~2s locally and produces embeddings good enough for genre-level clustering.

**Why sort books before UMAP?** Goodreads RSS returns books in a different order each request. UMAP is sensitive to input order even with `random_state=42`. Alphabetical sort before embedding ensures the map layout is reproducible.

**Why parse `<user_review>` from RSS?** The Goodreads RSS feed includes written reviews inside a `<user_review>` tag — not just star ratings. Including review text in the embedding input significantly improves cluster quality (Emily May's dataset: 149/197 books had review text).

**Why an opt-in judge?** Qwen 2.5 7B running locally pegs a laptop CPU for ~2 minutes. Making it opt-in means the main analysis completes in ~5 seconds (Cerebras is fast), and the judge is there for when you want the deeper comparison.
