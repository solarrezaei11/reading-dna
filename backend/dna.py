"""Reading DNA profile generation.

Notable correctness fixes:
  - The book sample shown to the LLM is a deterministic, representative
    stratified sample across rating buckets and recency, not just the
    highest-rated N (which biases the profile toward "what they loved" and
    ignores what a broad reading history actually looks like). The sampling
    logic itself lives in sampling.py, shared with llm_battle.py, so both
    modules import a common implementation instead of one importing from
    the other (which would create a dna <-> llm_battle circular import).
  - contrarian_score is only computed/claimed when there's enough Goodreads
    average-rating data to support a real consensus comparison; otherwise
    the prompt explicitly tells the model the signal is unavailable and the
    field is forced to null in the response.
  - The LLM's raw JSON response is validated against a strict Pydantic model
    (DnaProfile) before anything downstream trusts it — a malformed/invalid
    response becomes an explicit ValueError, never a crash or a silently
    accepted plausible-looking default.
"""
import asyncio
import json
import logging
from typing import Optional

from cerebras.cloud.sdk import CerebrasError
from pydantic import ValidationError

from config import MAX_REVIEW_EXCERPT_CHARS
from error_safety import safe_exception_summary, validation_error_summary
from llm_client import call_with_limit
from models import DnaProfile
from prompt_safety import guarded_system_prompt, sanitize_for_prompt
from sampling import build_book_summary as _build_book_summary

logger = logging.getLogger(__name__)

SAMPLE_TARGET = 80
REVIEW_EXCERPT_CHARS = min(200, MAX_REVIEW_EXCERPT_CHARS)

# A contrarian score requires a real consensus baseline. We don't compute or
# ask the LLM to compute one unless a meaningful share of the shelf carries a
# Goodreads average rating.
CONSENSUS_MIN_BOOKS = 8
CONSENSUS_MIN_RATIO = 0.3

_client = None


def _get_client():
    global _client
    if _client is None:
        import os

        from cerebras.cloud.sdk import AsyncCerebras

        _client = AsyncCerebras(api_key=os.environ.get("CEREBRAS_API_KEY"))
    return _client


def build_book_summary(books: list[dict], target: int = SAMPLE_TARGET) -> str:
    return _build_book_summary(books, target, REVIEW_EXCERPT_CHARS)


def _has_consensus_data(books: list[dict]) -> tuple[bool, list[dict]]:
    with_avg = [b for b in books if (b.get("avg_rating") or 0) > 0]
    if not books:
        return False, with_avg
    ratio = len(with_avg) / len(books)
    return (len(with_avg) >= CONSENSUS_MIN_BOOKS and ratio >= CONSENSUS_MIN_RATIO), with_avg


def _build_contrarian_section(books: list[dict]) -> tuple[str, bool]:
    """Returns (prompt section text, has_consensus). When there isn't enough
    average-rating data, the section explicitly instructs the model that the
    signal is unavailable rather than letting it invent a plausible-looking
    number."""
    has_consensus, with_avg = _has_consensus_data(books)
    total = len(books)

    if not has_consensus:
        return (
            f"\nCONSENSUS COMPARISON: Not enough Goodreads average-rating data is available "
            f"({len(with_avg)} of {total} books have it) to reliably measure how this reader compares "
            f"to popular consensus.\nSet \"contrarian_score\" to null — do NOT estimate or guess a "
            f"contrarian score without this evidence.\n",
            False,
        )

    diffs = sorted(
        (
            {
                "title": sanitize_for_prompt(b.get("title", "")),
                "my_rating": b.get("my_rating", 0),
                "avg_rating": b.get("avg_rating", 0.0),
                "delta": round((b.get("my_rating", 0) or 0) - (b.get("avg_rating", 0.0) or 0.0), 2),
            }
            for b in with_avg
        ),
        key=lambda d: (-abs(d["delta"]), d["title"].lower()),
    )[:10]
    avg_delta = sum(d["delta"] for d in diffs) / len(diffs) if diffs else 0.0
    lines = "\n".join(
        f'- "{d["title"]}": you rated {d["my_rating"]}/5 vs Goodreads consensus {d["avg_rating"]:.2f}/5 '
        f'(delta {d["delta"]:+.2f})'
        for d in diffs
    )
    return (
        f"\nCONSENSUS COMPARISON — your rating vs. Goodreads average rating, largest deltas "
        f"({len(with_avg)} of {total} books have Goodreads average-rating data):\n{lines}\n"
        f"Average signed delta (your rating minus Goodreads average): {avg_delta:+.2f}\n"
        f"Use this evidence to compute contrarian_score (how often they rate against popular consensus).\n",
        True,
    )


def _extract_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
        raise


def build_dna_prompt(
    currently_reading: list[dict],
    dnf: list[dict],
    summary: str,
    total: int,
    avg: float,
    high_rated: list[dict],
    low_rated: list[dict],
    contrarian_section: str,
) -> str:
    """Assemble the Reading DNA user prompt. A standalone, side-effect-free
    function (no network call) so its untrusted-data handling is directly
    unit-testable.

    Titles/authors sourced from Goodreads (currently-reading, DNF) are
    untrusted and run through sanitize_for_prompt() before being embedded,
    same as the rated-books summary — see sampling.format_book_line.
    """
    currently_reading_section = ""
    if currently_reading:
        titles = ", ".join(
            f'"{sanitize_for_prompt(b.get("title", ""))}" by {sanitize_for_prompt(b.get("author", ""))}'
            for b in currently_reading[:10]
        )
        currently_reading_section = f"\nCURRENTLY READING (strong active interest signal — these grabbed them enough to start):\n{titles}\n"

    dnf_section = ""
    if dnf:
        titles = ", ".join(
            f'"{sanitize_for_prompt(b.get("title", ""))}" by {sanitize_for_prompt(b.get("author", ""))}'
            for b in dnf[:10]
        )
        dnf_section = f"\nDID NOT FINISH (engagement/friction signal — something didn't hold their attention; NOT a dislike signal, more about pacing, style, or timing):\n{titles}\n"

    return f"""You are a literary analyst. Analyze this reader's Goodreads history and return a structured JSON Reading DNA profile.

RATED BOOKS (a representative sample across rating levels and recency, up to {SAMPLE_TARGET} of {total} total):
{summary}

Total books rated: {total}
Average rating given: {avg:.2f}/5
Books rated 4-5 stars: {len(high_rated)}
Books rated 1-2 stars: {len(low_rated)}
{currently_reading_section}{dnf_section}{contrarian_section}
Use currently-reading books as a signal of what actively excites them right now.
Use DNF books as a subtle friction signal about what styles or pacing didn't sustain their engagement — NOT as dislikes.

Return ONLY valid JSON with this exact structure, no markdown fences:
{{
  "reader_archetype": "A short evocative label (e.g. 'The Melancholic Intellectual', 'The Escapist Adventurer')",
  "one_liner": "One sentence describing this reader's taste personality",
  "taste_dimensions": {{
    "prose_density": <1-10, 1=breezy 10=dense/literary>,
    "pacing_preference": <1-10, 1=slow-burn 10=fast-paced>,
    "fiction_ratio": <0-100 percent fiction estimate>,
    "intellectual_depth": <1-10>,
    "emotional_intensity": <1-10>,
    "contrarian_score": <1-10, or null if the consensus comparison above says data is unavailable>
  }},
  "top_themes": ["theme1", "theme2", "theme3", "theme4", "theme5"],
  "avoid_themes": ["theme1", "theme2"],
  "favorite_authors": ["author1", "author2", "author3"],
  "taste_summary": "2-3 sentences describing what this reader loves and why, written in second person (You...)",
  "blind_spot_genres": ["genre1", "genre2"],
  "top_books": [
    {{"title": "...", "author": "...", "why_loved": "one sentence"}}
  ]
}}

top_books should be the 3 most loved books (5-star or highest rated).
blind_spot_genres are genres that critically acclaimed readers with similar taste often love but this reader hasn't explored."""


DNA_SYSTEM_PROMPT = guarded_system_prompt(
    "You are a literary analyst. Always respond with valid JSON only, no markdown."
)


async def build_dna_profile(
    books: list[dict],
    currently_reading: Optional[list[dict]] = None,
    dnf: Optional[list[dict]] = None,
) -> dict:
    currently_reading = currently_reading or []
    dnf = dnf or []
    books = [book for book in books if 1 <= book.get("my_rating", 0) <= 5]
    if not books:
        raise ValueError("Reading DNA requires at least one completed book with a 1-5 rating.")

    summary = build_book_summary(books)
    total = len(books)
    avg = sum(b.get("my_rating", 0) for b in books) / total if total else 0
    high_rated = [b for b in books if b.get("my_rating", 0) >= 4]
    low_rated = [b for b in books if b.get("my_rating", 0) <= 2]

    contrarian_section, has_consensus = _build_contrarian_section(books)
    prompt = build_dna_prompt(currently_reading, dnf, summary, total, avg, high_rated, low_rated, contrarian_section)

    client = _get_client()
    try:
        resp = await call_with_limit(
            client.chat.completions.create(
                model="gpt-oss-120b",
                messages=[
                    {"role": "system", "content": DNA_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
            )
        )
    except asyncio.TimeoutError:
        raise RuntimeError("Reading DNA generation timed out waiting for the LLM.") from None
    except CerebrasError as exc:
        summary = safe_exception_summary(exc)
        logger.warning("Reading DNA LLM call failed: %s", summary)
        raise RuntimeError(f"Reading DNA generation failed ({summary}).") from None
    text = resp.choices[0].message.content.strip()
    try:
        profile = _extract_json_object(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            f"Reading DNA response was not valid JSON ({safe_exception_summary(exc)})."
        ) from exc
    if not isinstance(profile, dict):
        raise ValueError(f"Reading DNA response must be a JSON object, got {type(profile).__name__}.")

    profile["total_books"] = total
    profile["avg_rating"] = round(avg, 2)

    # Validate the raw LLM shape before touching nested fields. Enrichment
    # must never turn malformed output (for example NaN or top_books=1) into
    # a plausible profile or an uncaught preprocessing error.
    try:
        raw_validated = DnaProfile.model_validate(profile)
    except ValidationError as exc:
        summary = validation_error_summary(exc)
        logger.warning("Reading DNA response failed validation: %s", summary)
        raise ValueError(f"Reading DNA response failed validation: {summary}") from exc

    validated_profile = raw_validated.model_dump()
    dims = validated_profile["taste_dimensions"]

    # Never let the model assert a contrarian score without real consensus
    # evidence, regardless of what it returned.
    if not has_consensus:
        dims["contrarian_score"] = None

    # Enrich top_books with real ISBNs from actual Goodreads data (LLM doesn't know these)
    isbn_map = {
        str(b.get("title") or "").strip().casefold(): b.get("isbn", "")
        for b in books
        if b.get("isbn") and str(b.get("title") or "").strip()
    }
    for tb in validated_profile["top_books"]:
        real_isbn = isbn_map.get(str(tb.get("title") or "").strip().casefold(), "")
        if real_isbn:
            tb["isbn"] = real_isbn

    try:
        validated = DnaProfile.model_validate(validated_profile)
    except ValidationError as exc:
        summary = validation_error_summary(exc)
        logger.warning("Reading DNA response failed validation: %s", summary)
        raise ValueError(f"Reading DNA response failed validation: {summary}") from exc

    return validated.model_dump()
