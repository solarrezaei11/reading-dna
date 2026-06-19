# ReadingDNA

Import your Goodreads history, get a taste profile, then watch two AI models battle to recommend your next book.

**GPT-OSS 120B vs GLM 4.7** — both running on Cerebras hardware. You see who knows you better.

---

<p align="center">
  <img src="public/screenshots/01_landing.png" alt="Landing page" width="49%" />
  <img src="public/screenshots/02_dna_profile.png" alt="Reading DNA profile" width="49%" />
</p>
<p align="center">
  <img src="public/screenshots/03_map.png" alt="Reading Universe Map with AI recommendations" width="98%" />
</p>

*Demo run on [Emily May's](https://www.goodreads.com/user/show/4622890-emily-may) public Goodreads profile — 197 books, archetype: **The Darkly Curious Intellectual**.*

---

## What it does

1. **Import** — paste your Goodreads profile URL (or export a CSV). Pulls read, currently-reading, and did-not-finish shelves.
2. **Profile** — local embeddings (`all-MiniLM-L6-v2`) + UMAP cluster your books into a Reading Universe Map. An LLM names each cluster and builds your Reading DNA (archetype, taste dimensions, themes).
3. **Battle** — GPT-OSS 120B and GLM 4.7 each recommend 5 books tailored to your profile. Consensus picks are highlighted; comfort-zone stretches get an amber glow.
4. **Share** — export a Spotify Wrapped-style card with your archetype and top reads.
5. **Availability** — checks OverDrive/Libby for recommended books at your local library.

## Stack

- **Frontend**: Next.js 14 (App Router) + TypeScript + Tailwind CSS + D3.js
- **Backend**: FastAPI + uvicorn
- **Embeddings**: `sentence-transformers` (local, no API cost)
- **Clustering**: UMAP + KMeans (`sklearn`)
- **LLMs**: Cerebras Cloud SDK — `gpt-oss-120b` and `zai-glm-4.7`

## Setup

### 1. Clone and install

```bash
git clone <repo-url>
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

Edit `.env.local` and add your [Cerebras API key](https://cloud.cerebras.ai).

### 4. Run

In two terminals:

```bash
# Terminal 1 — backend
cd backend && source venv/bin/activate && uvicorn main:app --reload

# Terminal 2 — frontend
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Goodreads setup

Your profile must be **public**. In Goodreads: Account → Settings → Privacy → set "Who can view my profile" to Everyone.

The RSS import uses your numeric user ID from the profile URL (e.g. `goodreads.com/user/show/12345678-your-name`).

## Notes

- The map layout is deterministic: books are sorted alphabetically before UMAP so the same library always produces the same map.
- Cluster naming uses `temperature=0` for stable labels across runs.
- Embeddings run locally — no external API calls for that step.
- `.env.local` is gitignored. Never commit API keys.
