"""OverDrive/Libby library lookup and ISBN availability checks.

Key correctness notes (see task description this module addresses, updated
with live API findings — see below):
  - The public /v2/libraries endpoint does NOT support a `query`/name filter.
    When we must search by name, we page through the full catalog
    (perPage=100) and match client-side. Live: totalItems ~= 12,977, i.e.
    ~130 pages at perPage=100 — LIBBY_CATALOG_MAX_PAGES must cover that.
  - GET /v2/libraries/{preferredKey} (singular) works as a direct lookup and
    is used as a fast path whenever the caller supplies a URL or bare key —
    this avoids paginating the ~13,000-library catalog for the common case
    of "I already know my library's key/URL".
  - Each catalog entry's `preferredKey` (NOT `websiteId`) is what the media
    endpoints require.
  - Media search results do NOT carry real-time availability (the
    `availability` field on search items is null) — the actual copy counts
    must be fetched separately from
    GET /v2/libraries/{key}/media/{titleId}/availability, whose fields are
    isAvailable, availableCopies, ownedCopies, holdsCount, estimatedWaitDays.
  - Catalog fetches are bounded (LIBBY_CATALOG_MAX_PAGES) and cached in
    memory with a TTL so repeated /libby calls don't re-page the whole
    OverDrive library directory every time.
"""
import asyncio
import difflib
import logging
import math
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

import httpx

from config import (
    LIBBY_CATALOG_FETCH_CONCURRENCY,
    LIBBY_CATALOG_MAX_PAGES,
    LIBBY_CATALOG_PER_PAGE,
    LIBBY_CATALOG_TTL_SECONDS,
    LIBBY_MEDIA_CONCURRENCY,
)
from error_safety import safe_exception_summary

logger = logging.getLogger(__name__)

LIBRARY_LIST_URL = "https://thunder.api.overdrive.com/v2/libraries"
LIBRARY_DETAIL_URL = "https://thunder.api.overdrive.com/v2/libraries/{preferred_key}"
MEDIA_URL = "https://thunder.api.overdrive.com/v2/libraries/{preferred_key}/media"
MEDIA_AVAILABILITY_URL = "https://thunder.api.overdrive.com/v2/libraries/{preferred_key}/media/{title_id}/availability"
USER_AGENT = "ReadingDNA/1.0"

# ISBN-10 (last char may be X) or ISBN-13, digits only after stripping hyphens/spaces.
_ISBN_RE = re.compile(r"^(?:\d{9}[\dXx]|\d{13})$")

# A reasonable, ordered format preference — try DRM ebook formats before
# audiobook/other so "available" reflects what most readers mean by it.
FORMAT_STRATEGY = ["ebook-epub-adobe", "ebook-epub-open", "ebook-overdrive", "ebook-kindle"]

_STOPWORDS = re.compile(r"\b(the|of|public|library|libraries|county|city|district)\b")
_WHITESPACE = re.compile(r"\s+")
_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,99}$")


def _normalize_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name or "")
    name = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).casefold().strip()
    name = "".join(character if character.isalnum() else " " for character in name)
    name = _STOPWORDS.sub(" ", name)
    return _WHITESPACE.sub(" ", name).strip()


def _safe_number(value, default=None):
    """Coerce a possibly-malformed numeric field (ownedCopies/holdsCount/
    estimatedWaitDays from an untrusted OverDrive response) to a number,
    falling back to `default` rather than letting a non-numeric value (a
    string, None, or an unexpected type) raise a TypeError during later
    arithmetic (-> 500)."""
    if value is None or isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) and number >= 0 else default


def is_valid_isbn(isbn: str) -> bool:
    isbn = (isbn or "").strip().replace("-", "").replace(" ", "")
    return bool(_ISBN_RE.match(isbn))


def _library_key(entry: dict) -> Optional[str]:
    """The media endpoint requires preferredKey — websiteId is NOT valid for that path."""
    value = entry.get("preferredKey") or entry.get("libraryKey")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _SAFE_PATH_SEGMENT.fullmatch(value) else None


def _library_display_name(entry: dict) -> str:
    for field_name in ("name", "title"):
        value = entry.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _library_key(entry) or ""


async def _fetch_library_detail(client: httpx.AsyncClient, key: str) -> Optional[dict]:
    """Direct single-library lookup via GET /v2/libraries/{preferredKey}.

    Confirmed working against the live API (e.g. key "toronto" -> Toronto
    Public Library). Used as a fast path so a caller who already has a
    URL/key doesn't force us to paginate the ~13,000-entry catalog.
    """
    try:
        resp = await client.get(
            LIBRARY_DETAIL_URL.format(preferred_key=key),
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
    except httpx.HTTPError as e:
        logger.warning("Direct library lookup failed: %s", safe_exception_summary(e))
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if isinstance(data, dict) and (data.get("preferredKey") or data.get("name")):
        return data
    return None


# ---------------------------------------------------------------------------
# Catalog loading (cached, paginated, bounded)
# ---------------------------------------------------------------------------

@dataclass
class _CatalogCache:
    libraries: list[dict] = field(default_factory=list)
    fetched_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Warnings describing THIS cached snapshot (e.g. partial-fetch,
    # page-cap-hit). Persisted alongside `libraries` so a cache-hit request
    # against a known-partial cache still surfaces the same warnings a
    # fresh fetch would have — a subsequent request must never silently
    # treat a known-partial cache as complete.
    warnings: list[str] = field(default_factory=list)
    total_items: Optional[int] = None

    def is_fresh(self) -> bool:
        return bool(self.libraries) and (time.monotonic() - self.fetched_at) < LIBBY_CATALOG_TTL_SECONDS


_catalog_cache = _CatalogCache()


async def _fetch_catalog_page(client: httpx.AsyncClient, page: int) -> tuple[list[dict], Optional[int]]:
    resp = await client.get(
        LIBRARY_LIST_URL,
        params={"perPage": LIBBY_CATALOG_PER_PAGE, "page": page},
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"catalog page {page} response was not a JSON object")
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise ValueError(f"catalog page {page} field 'items' was not a list")
    if any(not isinstance(item, dict) for item in raw_items):
        raise ValueError(f"catalog page {page} contained a malformed library entry")
    items = raw_items
    raw_total = data.get("totalItems") or data.get("total")
    total = (
        int(raw_total)
        if isinstance(raw_total, (int, float))
        and not isinstance(raw_total, bool)
        and math.isfinite(float(raw_total))
        and raw_total >= 0
        else None
    )
    return items, total


async def _fetch_catalog_sequential(
    client: httpx.AsyncClient, start_page: int
) -> tuple[list[dict], list[str]]:
    """Fetch pages one at a time starting at `start_page`, stopping at the
    first short/empty page or the max-page cap. Used when totalItems is
    missing/unreliable and we can't safely compute how many pages remain to
    fetch concurrently."""
    libraries: list[dict] = []
    warnings: list[str] = []
    page = start_page
    while page <= LIBBY_CATALOG_MAX_PAGES:
        try:
            items, _ = await _fetch_catalog_page(client, page)
        except (httpx.HTTPError, ValueError) as e:
            summary = safe_exception_summary(e)
            logger.warning("Libby catalog page %s failed: %s", page, summary)
            warnings.append(
                f"Catalog page {page} failed to load ({summary}); results may be incomplete."
            )
            break
        if not items:
            break
        libraries.extend(items)
        if len(items) < LIBBY_CATALOG_PER_PAGE:
            break
        page += 1
    else:
        warnings.append(
            f"Libby catalog fetch hit the page cap ({LIBBY_CATALOG_MAX_PAGES} pages); catalog may be incomplete."
        )
    return libraries, warnings


async def load_catalog(force_refresh: bool = False) -> tuple[list[dict], list[str]]:
    """Load (and cache) the OverDrive library catalog.

    Fetches page 1 alone (to learn totalItems), then — when that metadata
    is reliable (page 1 came back full-size) — fetches the remaining pages
    CONCURRENTLY, bounded by LIBBY_CATALOG_FETCH_CONCURRENCY, so a free-text
    library-name lookup needing the full ~130-page directory doesn't take
    ~130 sequential round trips (which can exceed a frontend request
    timeout). Falls back to the previous sequential stop-on-short-page
    approach when totalItems is missing or unreliable. Page order is always
    deterministic (re-sorted by page number before concatenation), and any
    single page's failure is caught, recorded as a warning, and simply
    omitted rather than aborting the whole fetch.

    A cache HIT always returns the warnings that were recorded when that
    cached snapshot was fetched (not an empty list) — a known-partial cache
    must never silently look complete just because it's still within its
    TTL.

    Returns (libraries, warnings).
    """
    if not force_refresh and _catalog_cache.is_fresh():
        return _catalog_cache.libraries, _catalog_cache.warnings

    async with _catalog_cache.lock:
        if not force_refresh and _catalog_cache.is_fresh():
            return _catalog_cache.libraries, _catalog_cache.warnings

        libraries: list[dict] = []
        warnings: list[str] = []
        total_items: Optional[int] = None

        async with httpx.AsyncClient() as client:
            try:
                first_items, total_items = await _fetch_catalog_page(client, 1)
            except (httpx.HTTPError, ValueError) as e:
                summary = safe_exception_summary(e)
                logger.warning("Libby catalog page 1 failed: %s", summary)
                warnings.append(
                    f"Libby catalog page 1 failed to load ({summary}); catalog is unavailable."
                )
                first_items, total_items = [], None

            libraries.extend(first_items)

            if len(first_items) < LIBBY_CATALOG_PER_PAGE:
                # Page 1 was already short (or empty/failed) — that's the
                # entire catalog (or all we could get); nothing more to fetch.
                pass
            elif total_items:
                total_pages = min(-(-total_items // LIBBY_CATALOG_PER_PAGE), LIBBY_CATALOG_MAX_PAGES)  # ceil division
                if total_pages > 1:
                    fetch_semaphore = asyncio.Semaphore(LIBBY_CATALOG_FETCH_CONCURRENCY)

                    async def _fetch_one(page: int) -> tuple[int, list[dict], Optional[str]]:
                        async with fetch_semaphore:
                            try:
                                items, _ = await _fetch_catalog_page(client, page)
                            except (httpx.HTTPError, ValueError) as e:
                                summary = safe_exception_summary(e)
                                logger.warning("Libby catalog page %s failed: %s", page, summary)
                                return (
                                    page,
                                    [],
                                    f"Catalog page {page} failed to load ({summary}); "
                                    "results may be incomplete.",
                                )
                            return page, items, None

                    fetched = await asyncio.gather(*(_fetch_one(p) for p in range(2, total_pages + 1)))
                    # Defensive re-sort by page number: asyncio.gather already
                    # preserves input order in its results, but sorting makes
                    # deterministic ordering an explicit invariant rather than
                    # an incidental property of the implementation.
                    for _page, items, warning in sorted(fetched, key=lambda t: t[0]):
                        libraries.extend(items)
                        if warning:
                            warnings.append(warning)
                    if total_pages >= LIBBY_CATALOG_MAX_PAGES:
                        warnings.append(
                            f"Libby catalog fetch hit the page cap ({LIBBY_CATALOG_MAX_PAGES} pages); catalog may be incomplete."
                        )
            else:
                # total_items missing/unreliable but page 1 was full-size —
                # we can't safely compute a page count, so fall back to
                # fetching sequentially until a short page or the cap.
                more, seq_warnings = await _fetch_catalog_sequential(client, 2)
                libraries.extend(more)
                warnings.extend(seq_warnings)

        if total_items and len(libraries) < total_items and not warnings:
            # A shortfall not already explained by one of the warnings
            # above (e.g. some pages silently returned fewer items than
            # requested without erroring) — surface it rather than silently
            # under-reporting the catalog.
            warnings.append(
                f"Only gathered {len(libraries)} of {total_items} known libraries; catalog may be incomplete."
            )

        if libraries:
            _catalog_cache.libraries = libraries
            _catalog_cache.warnings = warnings
            _catalog_cache.total_items = total_items
            _catalog_cache.fetched_at = time.monotonic()
        return _catalog_cache.libraries or libraries, warnings


# ---------------------------------------------------------------------------
# Library name / URL / key resolution
# ---------------------------------------------------------------------------

def parse_library_url_or_key(raw: str) -> Optional[str]:
    """Extract a library key directly from a Libby/OverDrive URL, or return a
    bare-key candidate. Returns None if `raw` looks like a plain library name."""
    raw = (raw or "").strip()
    if not raw:
        return None

    if re.match(r"^https?://", raw, re.IGNORECASE):
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower()
        path_parts = [p for p in parsed.path.split("/") if p]

        # Exact-host-or-subdomain match — a naive `.endswith("libbyapp.com")`
        # is substring-suffix vulnerable (e.g. "evillibbyapp.com" also
        # ends with "libbyapp.com"). Require an exact match or a real
        # subdomain (leading dot), same pattern already used below for
        # .overdrive.com.
        if (host == "libbyapp.com" or host.endswith(".libbyapp.com")) and path_parts:
            # https://libbyapp.com/library/<key>[/...]
            if path_parts[0] == "library" and len(path_parts) > 1:
                candidate = path_parts[1]
            else:
                candidate = path_parts[-1]
            return candidate if _SAFE_PATH_SEGMENT.fullmatch(candidate) else None

        if host.endswith(".overdrive.com") and not host.startswith("thunder."):
            # https://<key>.overdrive.com/...
            sub = host.split(".")[0]
            if sub != "www" and _SAFE_PATH_SEGMENT.fullmatch(sub):
                return sub

        return None

    # A bare key candidate: no spaces, short token of alnum/hyphen — library
    # *names* almost always contain a space, so this heuristic rarely misfires.
    if " " not in raw and _SAFE_PATH_SEGMENT.fullmatch(raw):
        return raw

    return None


def match_library(libraries: list[dict], query: str) -> tuple[Optional[dict], list[dict]]:
    """Match a free-text library name against the catalog.

    Tries exact normalized match, then substring match, then fuzzy match.
    Returns (best_match, alternatives) — best_match is None whenever the
    match is ambiguous (multiple equally-good candidates) so the caller can
    surface alternatives instead of silently picking an unrelated library.
    """
    q_norm = _normalize_name(query)
    if not q_norm:
        return None, []

    exact = [lib for lib in libraries if _normalize_name(_library_display_name(lib)) == q_norm]
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, exact[:10]

    contains = [lib for lib in libraries if q_norm in _normalize_name(_library_display_name(lib))]
    if len(contains) == 1:
        return contains[0], []
    if len(contains) > 1:
        return None, contains[:10]

    names = [_library_display_name(lib) for lib in libraries]
    normalized_names = [_normalize_name(n) for n in names]
    close = difflib.get_close_matches(q_norm, normalized_names, n=5, cutoff=0.72)
    if close:
        candidates = [libraries[normalized_names.index(cn)] for cn in close]
        if len(candidates) == 1:
            return candidates[0], []
        return None, candidates

    return None, []


async def resolve_library(library_name: str) -> dict:
    """Resolve a user-supplied library name, Libby/OverDrive URL, or bare key.

    Returns {"found": bool, "library": dict|None, "alternatives": list[dict], "warnings": list[str]}.

    Direct URL/key inputs take a fast path (a single GET, confirmed working
    against the live API) instead of paginating the full ~13,000-library
    catalog — this path never produces catalog warnings. Only free-text name
    queries fall back to the paginated catalog + client-side matching, and
    any partial-catalog-fetch warnings are surfaced here.
    """
    direct_key = parse_library_url_or_key(library_name)

    if direct_key:
        async with httpx.AsyncClient() as client:
            direct_lib = await _fetch_library_detail(client, direct_key)
        if direct_lib:
            return {"found": True, "library": direct_lib, "alternatives": [], "warnings": []}

    libraries, catalog_warnings = await load_catalog()

    if direct_key:
        for lib in libraries:
            if _library_key(lib) == direct_key:
                return {"found": True, "library": lib, "alternatives": [], "warnings": catalog_warnings}

    match, alternatives = match_library(libraries, library_name)
    if match:
        return {"found": True, "library": match, "alternatives": [], "warnings": catalog_warnings}
    return {"found": False, "library": None, "alternatives": alternatives, "warnings": catalog_warnings}


# ---------------------------------------------------------------------------
# Media / ISBN availability
# ---------------------------------------------------------------------------

def _select_best_item(items: list[dict], isbn: str) -> Optional[dict]:
    """Prefer an item whose own ISBN matches the query; else the first result.

    Defensive against a malformed items list: entries that aren't dicts are
    skipped rather than raising, and an empty/all-invalid list returns None
    instead of an IndexError.
    """
    valid_items = [it for it in items if isinstance(it, dict)]
    for item in valid_items:
        item_isbns = item.get("isbns") or ([item["isbn"]] if item.get("isbn") else [])
        if not isinstance(item_isbns, list):
            item_isbns = []
        if isbn in item_isbns:
            return item
    return valid_items[0] if valid_items else None


async def _check_isbn(
    client: httpx.AsyncClient,
    isbn: str,
    preferred_key: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, dict]:
    """Check one ISBN's availability at a library.

    Live API finding: media search results do NOT carry real-time
    availability (the `availability` field on search items is null) — this
    is a two-step process: (1) search media by identifier to find the
    title's id, then (2) fetch real availability from the dedicated
    /media/{titleId}/availability endpoint, whose fields are isAvailable,
    availableCopies, ownedCopies, holdsCount, estimatedWaitDays.
    """
    if not is_valid_isbn(isbn):
        return isbn, {"status": "invalid_isbn", "available": False, "wait_weeks": None}
    if not _SAFE_PATH_SEGMENT.fullmatch(preferred_key):
        return isbn, {
            "status": "error",
            "available": False,
            "wait_weeks": None,
            "error": "library key had an invalid format",
        }

    async with semaphore:
        try:
            search_resp = await client.get(
                MEDIA_URL.format(preferred_key=preferred_key),
                params={"identifier": isbn, "perPage": 5, "format": ",".join(FORMAT_STRATEGY)},
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
        except httpx.HTTPError as e:
            return isbn, {
                "status": "error",
                "available": False,
                "wait_weeks": None,
                "error": safe_exception_summary(e),
            }

        if search_resp.status_code == 404:
            # A clean "this library's media search has nothing for this
            # identifier" — a legitimate, confirmed no-match.
            return isbn, {"status": "not_in_catalog", "available": False, "wait_weeks": None}
        if search_resp.status_code != 200:
            # 429 (rate-limited) / 5xx (OverDrive outage) / any other
            # non-200 is NOT the same thing as a confirmed no-match — it
            # means we couldn't actually check, which the caller/reader
            # should be able to tell apart from "not in catalog".
            return isbn, {
                "status": "error",
                "available": False,
                "wait_weeks": None,
                "error": f"media search failed (HTTP {search_resp.status_code})",
            }

        try:
            data = search_resp.json()
        except ValueError as e:
            return isbn, {
                "status": "error",
                "available": False,
                "wait_weeks": None,
                "error": f"invalid JSON from OverDrive ({safe_exception_summary(e)})",
            }

        if not isinstance(data, dict):
            return isbn, {"status": "error", "available": False, "wait_weeks": None, "error": "media search response was not a JSON object"}

        raw_items = data.get("items")
        if not isinstance(raw_items, list):
            return isbn, {
                "status": "error",
                "available": False,
                "wait_weeks": None,
                "error": "media search field 'items' was not a list",
            }
        if any(not isinstance(item, dict) for item in raw_items):
            return isbn, {
                "status": "error",
                "available": False,
                "wait_weeks": None,
                "error": "media search contained a malformed item",
            }
        items = raw_items
        if not items:
            return isbn, {"status": "not_in_catalog", "available": False, "wait_weeks": None}

        item = _select_best_item(items, isbn)
        title_id = item.get("id") or item.get("titleId") if item else None
        if isinstance(title_id, int) and not isinstance(title_id, bool):
            title_id = str(title_id)
        if not isinstance(title_id, str) or not _SAFE_PATH_SEGMENT.fullmatch(title_id):
            logger.warning("OverDrive media search returned no usable title id.")
            return isbn, {
                "status": "error",
                "available": False,
                "wait_weeks": None,
                "error": "media search returned an invalid title id",
            }

        try:
            avail_resp = await client.get(
                MEDIA_AVAILABILITY_URL.format(preferred_key=preferred_key, title_id=title_id),
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
        except httpx.HTTPError as e:
            return isbn, {
                "status": "error",
                "available": False,
                "wait_weeks": None,
                "error": safe_exception_summary(e),
            }

    if avail_resp.status_code == 404:
        # The title exists in OverDrive's catalog but this library doesn't hold it.
        return isbn, {"status": "not_in_catalog", "available": False, "wait_weeks": None}
    if avail_resp.status_code != 200:
        return isbn, {
            "status": "error",
            "available": False,
            "wait_weeks": None,
            "error": f"availability check failed (HTTP {avail_resp.status_code})",
        }

    try:
        availability = avail_resp.json()
    except ValueError as e:
        return isbn, {
            "status": "error",
            "available": False,
            "wait_weeks": None,
            "error": (
                "invalid JSON from OverDrive availability endpoint "
                f"({safe_exception_summary(e)})"
            ),
        }

    if not isinstance(availability, dict):
        return isbn, {
            "status": "error",
            "available": False,
            "wait_weeks": None,
            "error": "availability response was not a JSON object",
        }

    raw_is_available = availability.get("isAvailable")
    if not isinstance(raw_is_available, bool):
        return isbn, {
            "status": "error",
            "available": False,
            "wait_weeks": None,
            "error": "availability field 'isAvailable' was not a boolean",
        }
    is_available = raw_is_available

    numeric_values: dict[str, float | None] = {}
    for field_name, default in (
        ("ownedCopies", 0.0),
        ("holdsCount", 0.0),
        ("estimatedWaitDays", None),
    ):
        raw_value = availability.get(field_name)
        parsed_value = _safe_number(raw_value, default=None)
        if raw_value is not None and parsed_value is None:
            return isbn, {
                "status": "error",
                "available": False,
                "wait_weeks": None,
                "error": f"availability field '{field_name}' was not a non-negative number",
            }
        numeric_values[field_name] = default if raw_value is None else parsed_value

    owned_copies = numeric_values["ownedCopies"] or 0.0
    holds_count = numeric_values["holdsCount"] or 0.0
    estimated_wait_days = numeric_values["estimatedWaitDays"]

    if is_available:
        return isbn, {"status": "available", "available": True, "wait_weeks": 0}
    if owned_copies > 0:
        # wait_weeks is only ever a REAL OverDrive-reported estimate
        # (estimatedWaitDays / 7), never an invented multiplier of
        # holds/copies — when OverDrive doesn't supply an estimate, we
        # return the raw counts instead of fabricating one.
        wait_weeks = max(1, round(estimated_wait_days / 7)) if estimated_wait_days is not None else None
        return isbn, {
            "status": "waitlist",
            "available": False,
            "wait_weeks": wait_weeks,
            "holds_count": holds_count,
            "owned_copies": owned_copies,
        }
    return isbn, {"status": "not_in_catalog", "available": False, "wait_weeks": None}


async def check_availability(isbns: list[str], library_name: str) -> dict:
    """POST /libby entry point. Returns the response shape the frontend expects:
    library_found, library_name, matched_library_name, library_key, alternatives,
    results (Record[isbn, result]), warnings (catalog incompleteness/partial
    failure surfaced here rather than only in logs)."""
    resolution = await resolve_library(library_name)
    catalog_warnings = resolution.get("warnings", [])

    if not resolution["found"]:
        alt_names = [_library_display_name(a) for a in resolution["alternatives"]]
        return {
            "library_found": False,
            "library_name": library_name,
            "matched_library_name": None,
            "library_key": None,
            "alternatives": alt_names,
            "results": {isbn: {"status": "library_not_found", "available": False} for isbn in isbns},
            "warnings": catalog_warnings,
        }

    library = resolution["library"]
    preferred_key = _library_key(library)
    matched_name = _library_display_name(library)

    if not preferred_key:
        return {
            "library_found": False,
            "library_name": library_name,
            "matched_library_name": matched_name,
            "library_key": None,
            "alternatives": [],
            "results": {isbn: {"status": "library_missing_key", "available": False} for isbn in isbns},
            "warnings": catalog_warnings,
        }

    semaphore = asyncio.Semaphore(LIBBY_MEDIA_CONCURRENCY)
    async with httpx.AsyncClient() as client:
        tasks = [_check_isbn(client, isbn, preferred_key, semaphore) for isbn in isbns]
        pairs = await asyncio.gather(*tasks)

    return {
        "library_found": True,
        "library_name": library_name,
        "matched_library_name": matched_name,
        "library_key": preferred_key,
        "alternatives": [],
        "results": dict(pairs),
        "warnings": catalog_warnings,
    }
