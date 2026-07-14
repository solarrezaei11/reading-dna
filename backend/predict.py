import os
import json
import time
import uuid
import asyncio
import logging
import numpy as np
from pydantic import ValidationError

from config import ENABLE_PREDICTION_LOG
from embeddings import embed_texts, book_to_text
from error_safety import safe_exception_summary, validation_error_summary
from llm_battle import _authors_plausibly_match, call_model, canonical_title, MODEL_INFO
from models import PredictionResponse
from open_library import lookup_open_library
from prompt_safety import guarded_system_prompt, sanitize_for_prompt

logger = logging.getLogger(__name__)

PREDICTIONS_LOG = os.path.join(os.path.dirname(__file__), "predictions.jsonl")

MODELS = ["gpt-oss-120b", "zai-glm-4.7"]

# A dedicated, guarded system prompt for rating-prediction calls — distinct
# from the recommender's system prompt (which talks about *recommending*
# books, not predicting a rating for one specific book), so this task gets
# instructions matched to what it's actually being asked to do.
PREDICTION_SYSTEM_PROMPT = guarded_system_prompt(
    "You are an expert at predicting how much a specific reader will enjoy a specific book, "
    "based on their reading history and taste profile. Always respond with valid JSON only, no markdown."
)


async def resolve_book(title: str, author: str | None = None) -> tuple[dict | None, str | None]:
    """Look up the book on Open Library to get author, year, subjects, ISBN, cover.

    Thin wrapper around the shared open_library.lookup_open_library (also
    used by llm_battle.py to verify/enrich recommendation ISBNs), preserved
    here under its original name/shape so existing call sites and tests are
    unaffected.

    Returns (candidate_or_None, warning_or_None). A warning distinguishes an
    Open Library outage or malformed response from a legitimate no-match: an
    empty `docs` list is a normal "not found" (no warning), while a network
    error or an unexpected response shape means we couldn't actually check —
    the caller falls back to unresolved metadata either way, but only the
    latter should surface as a warning.
    """
    candidate, warning = await lookup_open_library(title=title, author=author, timeout=15.0)
    if warning:
        return None, f"{warning}; using unresolved book metadata."
    if candidate and not candidate.get("author") and author:
        # Prediction display can retain the user's queried author, but the
        # shared lookup itself keeps missing author evidence empty so ISBN
        # verification never treats query text as an independent match.
        candidate = {**candidate, "author": author}
    return candidate, None


def find_already_read(title: str, books: list[dict], author: str | None = None) -> dict | None:
    """Check whether `title` is already on the user's shelf.

    When the caller supplies an `author` (now sent by the frontend), match
    on a canonical title equality *and* a plausible author match — this
    disambiguates books that share an ambiguous/common title (e.g. more than
    one novel titled "Evelina" or "Circe") rather than conflating them with
    whatever same-titled book happens to be on the shelf. `_authors_plausibly_match`
    is deliberately lenient about formatting ("J.R.R. Tolkien" vs "Tolkien, J. R. R.")
    so it only rules out books that are clearly by a different author.

    Falls back to the original title-only (substring-tolerant) matching only
    when no author was provided, preserving prior behavior for callers/tests
    that don't have one.
    """
    if author:
        q_title = canonical_title(title)
        for b in books:
            if canonical_title(b.get("title", "")) == q_title and _authors_plausibly_match(author, b.get("author", "")):
                return b
        return None

    q = title.lower().strip()
    for b in books:
        t = b.get("title", "").lower().strip()
        if q == t or (len(q) > 8 and len(t) > 8 and (q in t or t in q)):
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
    """`candidate`/`neighbors` come from Open Library search results and the
    user's own shelf respectively — both untrusted text — and `dna` is
    LLM-generated from the same Goodreads data. Every interpolated field is
    sanitized here as defense in depth against injected instructions."""
    dims = dna.get("taste_dimensions", {})
    evidence = "\n".join(
        f'- "{sanitize_for_prompt(n.get("title", ""))}" by {sanitize_for_prompt(n.get("author", ""))} '
        f'— reader rated {n["my_rating"]}/5 (similarity {n["similarity"]})'
        for n in neighbors
        if n["my_rating"]
    )
    subjects = ", ".join(sanitize_for_prompt(str(s)) for s in candidate.get("subjects", [])) or "unknown"
    year = candidate.get("year") or "unknown"
    archetype = sanitize_for_prompt(str(dna.get("reader_archetype") or ""))
    taste_summary = sanitize_for_prompt(str(dna.get("taste_summary") or ""))
    top_themes = ", ".join(sanitize_for_prompt(str(t)) for t in dna.get("top_themes", []) or [])
    avoid_themes = ", ".join(sanitize_for_prompt(str(t)) for t in dna.get("avoid_themes", []) or [])
    candidate_title = sanitize_for_prompt(str(candidate.get("title", "")))
    candidate_author = sanitize_for_prompt(str(candidate.get("author", "")))

    return f"""You are predicting whether a specific reader will enjoy a book they have NOT read yet.

READER PROFILE:
- Archetype: {archetype}
- Taste summary: {taste_summary}
- Top themes: {top_themes}
- Themes to avoid: {avoid_themes}
- Prose density preference: {dims.get('prose_density')}/10
- Pacing preference: {dims.get('pacing_preference')}/10
- Intellectual depth: {dims.get('intellectual_depth')}/10
- Emotional intensity: {dims.get('emotional_intensity')}/10
- Fiction ratio: {dims.get('fiction_ratio')}%
- Average rating this reader gives: {avg_rating:.2f}/5

CANDIDATE BOOK:
- Title: {candidate_title}
- Author: {candidate_author}
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
    """Append a prediction record to the local JSONL log.

    Opt-in via ENABLE_PREDICTION_LOG — disabled by default so this app
    doesn't silently accumulate a global, multi-user log of every prediction
    ever made. There is no public endpoint that exposes this file; it exists
    purely as an optional local diagnostic/tuning aid.
    """
    if not ENABLE_PREDICTION_LOG:
        return
    try:
        with open(PREDICTIONS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError) as e:
        # Logging must never break the request (but CancelledError/other
        # BaseExceptions must still propagate — only ordinary Exceptions
        # from serialization/file I/O are swallowed here).
        logger.warning("Prediction logging failed: %s", safe_exception_summary(e))


def load_predictions() -> list[dict]:
    if not os.path.exists(PREDICTIONS_LOG):
        return []
    entries = []
    with open(PREDICTIONS_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


async def predict_rating(title: str, author: str | None, dna: dict, books: list[dict]) -> dict:
    t_start = time.perf_counter()
    stages: dict = {}

    # Already on their shelf?
    already = find_already_read(title, books, author)
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
    t0 = time.perf_counter()
    resolved, resolve_warning = await resolve_book(title, author)
    stages["resolve_ms"] = round((time.perf_counter() - t0) * 1000)
    candidate = resolved or {"title": title, "author": author or "Unknown", "subjects": [], "year": None, "isbn": None}

    # Stage 2: embedding neighbors
    t0 = time.perf_counter()
    neighbors = await nearest_neighbors(candidate, books) if books else []
    stages["embed_ms"] = round((time.perf_counter() - t0) * 1000)

    # Stage 3: both models predict in parallel
    rated = [b.get("my_rating", 0) for b in books if b.get("my_rating")]
    avg_rating = sum(rated) / len(rated) if rated else 3.5
    prompt = build_predict_prompt(dna, candidate, neighbors, avg_rating)

    t0 = time.perf_counter()
    results = await asyncio.gather(
        *(call_model(m, prompt, system_prompt=PREDICTION_SYSTEM_PROMPT) for m in MODELS),
        return_exceptions=True,
    )
    stages["llm_ms"] = round((time.perf_counter() - t0) * 1000)
    stages["total_ms"] = round((time.perf_counter() - t_start) * 1000)

    predictions: dict = {}
    for model_id, res in zip(MODELS, results, strict=True):
        display = MODEL_INFO.get(model_id, {}).get("display", model_id)
        # Cancellation must never be mistaken for an ordinary model error —
        # re-raise it so the caller (and asyncio) see the request was
        # actually cancelled, not that the model "failed".
        if isinstance(res, asyncio.CancelledError):
            raise res
        if isinstance(res, BaseException):
            predictions[display] = {"error": safe_exception_summary(res)}
            continue

        meta = res.pop("_meta", {})
        try:
            validated = PredictionResponse.model_validate(res)
        except ValidationError as e:
            first_error = e.errors()[0] if e.errors() else {}
            logger.warning(
                "%s returned an invalid prediction payload: %s",
                model_id,
                validation_error_summary(e),
            )
            predictions[display] = {
                "error": f"Model returned an invalid prediction ({first_error.get('msg', 'validation error')}).",
                "meta": meta,
            }
            continue

        predictions[display] = {
            "predicted_rating": validated.predicted_rating,
            "confidence": validated.confidence,
            "why": validated.why,
            "drivers": [d.model_dump() for d in validated.drivers],
            "meta": meta,
        }

    warnings: list[str] = []
    if resolve_warning:
        warnings.append(resolve_warning)

    payload = {
        "already_read": False,
        "id": uuid.uuid4().hex[:8],
        "book": candidate,
        "resolved": resolved is not None,
        "predictions": predictions,
        "neighbors": neighbors,
        "reader_avg_rating": round(avg_rating, 2),
        "stages": stages,
        "warnings": warnings,
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
