# Contributing to ReadingDNA

Thanks for your interest in contributing! This guide gets you from clone to a
passing pull request.

## Ways to help

- Pick up an issue labeled [`good first issue`](https://github.com/solarrezaei11/reading-dna/labels/good%20first%20issue)
  — these are scoped to be approachable without deep knowledge of the codebase.
- Improve docs, add tests, or file a well-described bug report.
- Larger features: please open an issue to discuss before sending a big PR.

## Project layout

| Path | What lives here |
| --- | --- |
| `app/`, `components/` | Next.js (App Router) frontend, TypeScript + React |
| `backend/` | FastAPI service: RSS/CSV parsing, DNA profile, model battle, embeddings map, judge |
| `backend/providers.py` | The model roster (`_ROSTER`) and per-provider settings |
| `backend/llm_battle.py` | The N-way recommendation battle and prompt building |
| `public/screenshots/` | README imagery |

## Prerequisites

- Node.js 20+
- Python 3.11+
- (Optional) [Ollama](https://ollama.com/) for the local judge

## Local setup

```bash
# 1. Frontend deps
npm install

# 2. Backend env
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cd ..

# 3. Configure environment
cp .env.example .env            # then fill in the keys you have
```

You do **not** need every provider key. A single `CEREBRAS_API_KEY` is enough
to run the app; adding `GROQ_API_KEY` or `OPENROUTER_API_KEY` transparently
adds those models as extra battle competitors.

**Never commit `.env` or any API key.** `.env` is gitignored — keep it that way.

## Running both services

```bash
# Backend (from backend/, venv active)
./start.sh                      # serves on http://127.0.0.1:8000

# Frontend (separate terminal, repo root)
npm run dev                     # serves on http://localhost:3000
```

## Validation — run before opening a PR

CI runs exactly these; green locally means green in CI.

**Frontend:**

```bash
npm run lint
npm run typecheck
npm test
npm run build
npm audit --audit-level=moderate
```

**Backend:**

```bash
cd backend
python -m pip check
python -m compileall -q .
python -m unittest discover -s tests -p "test_*.py" -v
```

Run the smallest set that covers your change, but make sure the full set passes
before you request review.

## Pull request checklist

- [ ] The change is focused and described clearly in the PR body.
- [ ] Relevant validation commands pass locally.
- [ ] New behavior has a test where practical.
- [ ] No secrets, keys, or `.env` files are included in the diff.
- [ ] Docs/README updated if behavior or setup changed.

## Commit style

Short, imperative subject lines (e.g. `fix: bound DNF titles in battle prompt`).
Keep unrelated changes in separate commits/PRs.

## Code of conduct

Be kind and constructive. Assume good intent, and keep reviews focused on the
code, not the person.
