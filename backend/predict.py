import os
import json
import time
import uuid
import asyncio
import numpy as np
import httpx

from embeddings import embed_texts, book_to_text
from llm_battle import call_model, MODEL_INFO

PREDICTIONS_LOG = os.path.join(os.path.dirname(__file__), "predictions.jsonl")

MODELS = ["gpt-oss-120b", "zai-glm-4.7"]


async def resolve_book(title: str, author: str | None = None) -> dict | None:
    """Look up the book on Open Library to get author, year, subjects, ISBN, cover."""
    params = {
        "title": title,
        "limit": "1",
        "fields": "title,author_name,first_publish_year,subject,isbn,cover_i",
    }
    if author:
        params["author"] = author
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.get("https://openlibrary.org/search.json", params=params)
            resp.raise_for_status()
            docs = resp.json().get("docs", [])
    except Exception:
        return None
    if not docs:
        return None
    doc = docs[0]
    isbns = doc.get("isbn") or []
    return {
        "title": doc.get("title", title),
        "author": (doc.get("author_name") or [author or "Unknown"])[0],
        "year": doc.get("first_publish_year"),
        "subjects": (doc.get("subject") or [])[:10],
        "isbn": isbns[0] if isbns else None,
        "cover_i": doc.get("cover_i"),
    }


def find_already_read(title: str, books: list[dict]) -> dict | None:
    q = title.lower().strip()
    for b in books:
        t = b.get("title", "").lower().strip()
        if q == t or (len(q) > 8 and (q in t or t in q)):
            return b
    return None


async def nearest_neighbors(candidate: dict, books: list[dict], k: int = 5) -> list[dict]:
    """Embed the candidate alongside the user's shelf, return k most similar books with the user's ratings."""
    texts = [book_to_text(b) for b in books]
    cand_parts = [candidate["title"], f"by {candidate['author']}"]
    if candidate.get("subjects"):
        cand_parts.append(", ".join(candidate["subjects"]))
    embs = await embed_texts(texts + [" | ".join(cand_parts)])
    shelf = embs[:-1]
    cand = embs[-1]
    # Cosine similarity
    shelf_norm = shelf / (np.linalg.norm(shelf, axis=1, keepdims=True) + 1e-8)
    cand_norm = cand / (np.linalg.norm(cand) + 1e-8)
    sims = shelf_norm @ cand_norm
    order = np.argsort(-sims)[:k]
    return [
        {
            "title": books[i]["title"],
            "author": books[i].get("author", ""),
            "my_rating": books[i].get("my_rating", 0),
            "similarity": round(float(sims[i]), 3),
        }
        for i in order
    ]


def build_predict_prompt(dna: dict, candidate: dict, neighbors: list[dict], avg_rating: float) -> str:
    dims = dna.get("taste_dimensions", {})
    evidence = "\n".join(
        f'- "{n["title"]}" by {n["author"]} — reader rated {n["my_rating"]}/5 (similarity {n["similarity"]})'
        for n in neighbors
        if n["my_rating"]
    )
    subjects = ", ".join(candidate.get("subjects", [])) or "unknown"
    year = candidate.get("year") or "unknown"

    return f"""You are predicting whether a specific reader will enjoy a book they have NOT read yet.

READER PROFILE:
- Archetype: {dna.get('reader_archetype')}
- Taste summary: {dna.get('taste_summary')}
- Top themes: {', '.join(dna.get('top_themes', []))}
- Themes to avoid: {', '.join(dna.get('avoid_themes', []))}
- Prose density preference: {dims.get('prose_density')}/10
- Pacing preference: {dims.get('pacing_preference')}/10
- Intellectual depth: {dims.get('intellectual_depth')}/10
- Emotional intensity: {dims.get('emotional_intensity')}/10
- Fiction ratio: {dims.get('fiction_ratio')}%
- Average rating this reader gives: {avg_rating:.2f}/5

CANDIDATE BOOK:
- Title: {candidate['title']}
- Author: {candidate['author']}
- First published: {year}
- Subjects: {subjects}

EVIDENCE — most similar books on this reader's shelf (by embedding similarity), with the rating THIS reader gave:
{evidence or "No close neighbors found on their shelf."}

Predict the rating this reader would give this book after finishing it. Calibrate against their average rating — a predicted 4.5 from a reader whose average is 3.2 is a much stronger claim than from a reader whose average is 4.4.

Return ONLY valid JSON, no markdown fences:
{{
  "predicted_rating": <float between 1.0 and 5.0, one decimal place>,
  "confidence": <float between 0 and 1>,
  "why": "<2-3 sentences tied to THIS reader's profile and shelf evidence>",
  "drivers": [
    {{"factor": "<short phrase>", "direction": "+"}},
    {{"factor": "<short phrase>", "direction": "-"}}
  ]
}}

Include 2-3 drivers, mixing positive and negative factors when both exist."""


def log_prediction(entry: dict) -> None:
    try:
        with open(PREDICTIONS_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Logging must never break the request


def load_predictions() -> list[dict]:
    if not os.path.exists(PREDICTIONS_LOG):
        return []
    entries = []
    with open(PREDICTIONS_LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


async def predict_rating(title: str, author: str | None, dna: dict, books: list[dict]) -> dict:
    t_start = time.time()
    stages: dict = {}

    # Already on their shelf?
    already = find_already_read(title, books)
    if already:
        return {
            "already_read": True,
            "book": {
                "title": already["title"],
                "author": already.get("author", ""),
                "isbn": already.get("isbn"),
            },
            "actual_rating": already.get("my_rating", 0),
        }

    # Stage 1: resolve via Open Library
    t0 = time.time()
    resolved = await resolve_book(title, author)
    stages["resolve_ms"] = round((time.time() - t0) * 1000)
    candidate = resolved or {"title": title, "author": author or "Unknown", "subjects": [], "year": None, "isbn": None}

    # Stage 2: embedding neighbors
    t0 = time.time()
    neighbors = await nearest_neighbors(candidate, books) if books else []
    stages["embed_ms"] = round((time.time() - t0) * 1000)

    # Stage 3: both models predict in parallel
    rated = [b.get("my_rating", 0) for b in books if b.get("my_rating")]
    avg_rating = sum(rated) / len(rated) if rated else 3.5
    prompt = build_predict_prompt(dna, candidate, neighbors, avg_rating)

    t0 = time.time()
    results = await asyncio.gather(
        *(call_model(m, prompt) for m in MODELS),
        return_exceptions=True,
    )
    stages["llm_ms"] = round((time.time() - t0) * 1000)
    stages["total_ms"] = round((time.time() - t_start) * 1000)

    predictions: dict = {}
    for model_id, res in zip(MODELS, results):
        display = MODEL_INFO.get(model_id, {}).get("display", model_id)
        if isinstance(res, Exception):
            predictions[display] = {"error": str(res)}
        else:
            meta = res.pop("_meta", {})
            predictions[display] = {
                "predicted_rating": res.get("predicted_rating"),
                "confidence": res.get("confidence"),
                "why": res.get("why"),
                "drivers": res.get("drivers", []),
                "meta": meta,
            }

    payload = {
        "already_read": False,
        "id": uuid.uuid4().hex[:8],
        "book": candidate,
        "resolved": resolved is not None,
        "predictions": predictions,
        "neighbors": neighbors,
        "reader_avg_rating": round(avg_rating, 2),
        "stages": stages,
    }

    log_prediction({
        "id": payload["id"],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "query": {"title": title, "author": author},
        "book": {k: candidate.get(k) for k in ("title", "author", "year", "isbn")},
        "predictions": {
            name: {k: p.get(k) for k in ("predicted_rating", "confidence")} | {"latency_ms": (p.get("meta") or {}).get("latency_ms")}
            for name, p in predictions.items()
        },
        "stages": stages,
        "outcome": None,  # filled in later when the reader reports back
    })

    return payload
