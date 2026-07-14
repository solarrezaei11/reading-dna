"""Shared Open Library HTTP lookup.

Extracted so it can be reused by both predict.py (resolving a book's
metadata for a rating prediction) and llm_battle.py (verifying/enriching
LLM-supplied recommendation ISBNs) without duplicating the request/parsing
logic or introducing a predict<->llm_battle circular import (predict.py
already imports call_model/MODEL_INFO from llm_battle.py).
"""
import asyncio
import logging
from typing import Optional

import httpx

from error_safety import safe_exception_summary

logger = logging.getLogger(__name__)

OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"

_SEARCH_FIELDS = "title,author_name,first_publish_year,subject,isbn,cover_i"

# Open Library asks API consumers to identify themselves; a UA also helps
# distinguish our traffic in the event of throttling/abuse investigation.
_USER_AGENT = "ReadingDNA/1.0 (+https://github.com/solarrezaei11/reading-dna)"


async def lookup_open_library(
    *,
    title: Optional[str] = None,
    author: Optional[str] = None,
    isbn: Optional[str] = None,
    limit: int = 1,
    timeout: float = 15.0,
) -> tuple[Optional[dict], Optional[str]]:
    """Look up a book on Open Library by title(+author) or by ISBN.

    Returns (candidate_or_None, warning_or_None). A warning distinguishes an
    Open Library outage or malformed response from a legitimate no-match: an
    empty `docs` list is a normal "not found" (no warning, candidate is
    None), while a network error or an unexpected response shape means we
    couldn't actually check — callers should treat that differently from a
    confirmed no-match (e.g. not silently dropping an unverified ISBN as if
    it were proven wrong).
    """
    if not isbn and not title:
        raise ValueError("lookup_open_library requires either isbn or title")

    params: dict[str, str] = {"limit": str(limit), "fields": _SEARCH_FIELDS}
    if isbn:
        params["isbn"] = isbn
    if title:
        params["title"] = title
    if author:
        params["author"] = author

    try:
        async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": _USER_AGENT}) as http:
            resp = await http.get(OPEN_LIBRARY_SEARCH_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except asyncio.CancelledError:
        raise  # never swallow cancellation
    except httpx.HTTPError as e:
        summary = safe_exception_summary(e)
        logger.warning("Open Library %s lookup failed: %s", "ISBN" if isbn else "title", summary)
        return None, f"Open Library lookup failed ({summary})"
    except (ValueError, KeyError, TypeError) as e:
        summary = safe_exception_summary(e)
        logger.warning("Open Library returned an invalid %s response: %s", "ISBN" if isbn else "title", summary)
        return None, f"Open Library returned an invalid response ({summary})"

    if not isinstance(payload, dict):
        return None, "Open Library returned an invalid response (not a JSON object)"

    docs = payload.get("docs")
    if not isinstance(docs, list):
        return None, "Open Library returned an invalid response ('docs' was not a list)"
    if not docs:
        return None, None  # legitimate no-match — nothing to warn about
    doc = docs[0]
    if not isinstance(doc, dict):
        return None, "Open Library returned an invalid response (malformed result entry)"

    raw_isbns = doc.get("isbn")
    if raw_isbns is None:
        isbns: list[str] = []
    elif isinstance(raw_isbns, list) and all(isinstance(value, str) for value in raw_isbns):
        isbns = raw_isbns
    else:
        return None, "Open Library returned an invalid response ('isbn' was not a list of strings)"

    raw_authors = doc.get("author_name")
    if raw_authors is None:
        authors: list[str] = []
    elif isinstance(raw_authors, list) and all(isinstance(value, str) for value in raw_authors):
        authors = raw_authors
    else:
        return None, "Open Library returned an invalid response ('author_name' was not a list of strings)"

    raw_subjects = doc.get("subject")
    if raw_subjects is None:
        subjects: list[str] = []
    elif isinstance(raw_subjects, list) and all(isinstance(value, str) for value in raw_subjects):
        subjects = raw_subjects
    else:
        return None, "Open Library returned an invalid response ('subject' was not a list of strings)"

    raw_title = doc.get("title", title)
    if not isinstance(raw_title, str) or not raw_title.strip():
        return None, "Open Library returned an invalid response (book title was missing)"

    raw_year = doc.get("first_publish_year")
    if raw_year is not None and (
        isinstance(raw_year, bool) or not isinstance(raw_year, (str, int))
    ):
        return None, "Open Library returned an invalid response ('first_publish_year' had an invalid type)"

    raw_cover_id = doc.get("cover_i")
    if raw_cover_id is not None and (
        isinstance(raw_cover_id, bool) or not isinstance(raw_cover_id, int)
    ):
        return None, "Open Library returned an invalid response ('cover_i' had an invalid type)"

    # Preserve the distinction between returned evidence and the query. The
    # caller-supplied author must not be substituted here or ISBN verification
    # could mistake the query itself for independent Open Library evidence.
    first_author = next((value.strip() for value in authors if value.strip()), "")
    first_isbn = next((value.strip() for value in isbns if value.strip()), None)
    return {
        "title": raw_title.strip(),
        "author": first_author,
        "year": raw_year,
        "subjects": [value.strip() for value in subjects[:10] if value.strip()],
        "isbn": first_isbn,
        "cover_i": raw_cover_id,
    }, None
