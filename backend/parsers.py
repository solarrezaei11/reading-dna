"""Goodreads CSV export + RSS shelf parsing.

Security note: `parse_rss` validates all user-supplied input (a Goodreads
profile URL or raw numeric user ID) before any network request is made, and
only ever contacts goodreads.com / www.goodreads.com over https. This closes
off blind SSRF via the RSS fetch path — an attacker cannot point this
endpoint at an internal address or an unrelated third-party host.
"""
import asyncio
import csv
import io
import re
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from fastapi import HTTPException

from config import (
    GOODREADS_RSS_MAX_PAGES,
    GOODREADS_RSS_PAGE_DELAY_SECONDS,
    GOODREADS_RSS_PER_PAGE,
    MAX_REVIEW_EXCERPT_CHARS,
)
from error_safety import safe_exception_summary

GOODREADS_HOSTS = {"goodreads.com", "www.goodreads.com"}
DNF_SHELF_KEYWORDS = {"did-not-finish", "dnf", "abandoned", "gave-up", "unfinished", "did not finish"}

# Only these are actual Goodreads *profile* paths (the ones this module
# resolves a user ID from). Restricting to them — rather than accepting any
# path on goodreads.com — means a validated "profile URL" can never become
# an arbitrary redirect-following fetch of an unrelated goodreads.com path
# (still same-host, so not classic SSRF, but still tightens what this input
# is allowed to make the backend fetch to exactly what it claims to be).
_ALLOWED_PROFILE_PATH_RE = re.compile(r"^/(user/show|review/list)(?:/|$)", re.IGNORECASE)

# Real Goodreads user IDs are positive decimal integers. Early accounts can
# have quite short IDs (a handful of digits) — there is no minimum digit
# count that makes an ID "valid" or "invalid"; the actual SSRF defense is
# the host/scheme canonicalization in validate_and_normalize_profile_input,
# not digit counting. This bound (1-20 digits, no leading zero, so "0"
# itself is rejected) exists only to reject obviously-malformed input
# (empty, zero, or unbounded numeric noise), not to guess a minimum length.
_USER_ID_DIGITS = r"[1-9]\d{0,19}"
_GOODREADS_USER_ID_RE = re.compile(rf"^{_USER_ID_DIGITS}$")


# ---------------------------------------------------------------------------
# CSV parsing (Goodreads library export)
# ---------------------------------------------------------------------------

def _safe_int(value: Optional[str], default: int = 0) -> int:
    value = (value or "").strip()
    if not value:
        return default
    try:
        return int(float(value))  # tolerates values like "4.0"
    except ValueError:
        return default


def _safe_float(value: Optional[str], default: float = 0.0) -> float:
    value = (value or "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _parse_csv_row(row: dict) -> Optional[dict]:
    """Return a book dict, None to skip (unrated), or raise ValueError if malformed."""
    rating = _safe_int(row.get("My Rating"), 0)
    if rating <= 0:
        return None  # unrated / not-yet-read row — not an error, just skip it

    title = (row.get("Title") or "").strip()
    if not title:
        raise ValueError("row has a rating but no title")

    date_read = (row.get("Date Read") or "").strip()
    year_read = None
    if date_read:
        try:
            # Goodreads exports "Date Read" as YYYY/MM/DD — the year is the
            # first component, not the last (which is the day).
            year_read = int(date_read.split("/")[0]) if "/" in date_read else int(date_read[:4])
        except (ValueError, IndexError):
            year_read = None

    isbn_raw = (row.get("ISBN13") or row.get("ISBN") or "").strip()
    isbn = isbn_raw.replace("=", "").replace('"', "").strip()

    # Truncate the review excerpt here — at parse time, before it's ever
    # returned to the browser/session — rather than relying solely on the
    # Book Pydantic model's later validator to catch an oversized value.
    my_review = (row.get("My Review") or "").strip()[:MAX_REVIEW_EXCERPT_CHARS]

    return {
        "title": title,
        "author": (row.get("Author") or "").strip(),
        "isbn": isbn,
        "my_rating": max(0, min(5, rating)),
        "avg_rating": max(0.0, min(5.0, _safe_float(row.get("Average Rating"), 0.0))),
        "num_pages": max(0, _safe_int(row.get("Number of Pages"), 0)),
        "year_published": (row.get("Original Publication Year") or "").strip(),
        "date_read": date_read,
        "year_read": year_read,
        "shelves": (row.get("Exclusive Shelf") or "").strip(),
        "my_review": my_review,
        "genres": [],
    }


def _parse_csv_rows(raw) -> tuple[list[dict], list[str]]:
    """Shared implementation behind parse_csv / parse_csv_with_warnings.

    Returns (books, warnings). Robust to a UTF-8 BOM (uses utf-8-sig),
    missing/invalid numeric fields (falls back to sane defaults instead of
    raising), and malformed rows (skipped, counted, and surfaced as a
    warning — and as a 400 rather than a 500 if nothing usable comes out).
    """
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise HTTPException(
                status_code=400,
                detail=f"CSV file is not valid UTF-8 text ({safe_exception_summary(e)}).",
            )
    else:
        text = (raw or "").lstrip("\ufeff")

    if not text.strip():
        raise HTTPException(status_code=400, detail="CSV file is empty.")

    try:
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = reader.fieldnames
    except csv.Error as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not parse CSV ({safe_exception_summary(e)}).",
        )

    if not fieldnames or "Title" not in fieldnames:
        raise HTTPException(
            status_code=400,
            detail="This doesn't look like a Goodreads export CSV (missing a 'Title' column).",
        )

    books: list[dict] = []
    malformed_rows = 0
    try:
        for row in reader:
            try:
                book = _parse_csv_row(row)
            except ValueError:
                malformed_rows += 1
                continue
            if book is not None:
                books.append(book)
    except csv.Error as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not parse CSV: malformed row data ({safe_exception_summary(e)}).",
        )

    if not books:
        detail = "No rated books found in this CSV."
        if malformed_rows:
            detail += f" ({malformed_rows} row(s) were malformed and skipped.)"
        raise HTTPException(status_code=400, detail=detail)

    warnings: list[str] = []
    if malformed_rows:
        warnings.append(
            f"{malformed_rows} row(s) were malformed (rated but missing a title) and were skipped."
        )

    return books, warnings


def parse_csv(raw) -> list[dict]:
    """Parse a Goodreads CSV export. Accepts bytes or str.

    Kept list-returning for backward compatibility with existing callers;
    use parse_csv_with_warnings when malformed/skipped-row visibility is
    needed (e.g. the /parse/csv route).
    """
    books, _warnings = _parse_csv_rows(raw)
    return books


def parse_csv_with_warnings(raw) -> tuple[list[dict], list[str]]:
    """Same parsing as parse_csv, but also returns warnings describing any
    malformed/skipped rows so the API can surface them instead of only
    logging (or silently dropping) them."""
    return _parse_csv_rows(raw)


# ---------------------------------------------------------------------------
# Input validation (SSRF hardening)
# ---------------------------------------------------------------------------

def validate_and_normalize_profile_input(raw: str) -> str:
    """Validate a user-supplied Goodreads profile URL or numeric ID.

    Accepted: a bounded positive numeric Goodreads user ID (1-20 digits, no
    leading zero — see _GOODREADS_USER_ID_RE; short IDs from early accounts
    are valid, there is no minimum digit count), or a goodreads.com /
    www.goodreads.com URL (scheme optional; normalized to https) whose path
    is an actual Goodreads *profile* path (/user/show/... or
    /review/list/...) — see _ALLOWED_PROFILE_PATH_RE. Anything else — other
    hosts, other schemes (file://, ftp://, internal IPs via a different
    host, etc.), an arbitrary non-profile goodreads.com path/query, zero, or
    unbounded numeric noise — is rejected before any network request is
    made. The host is always canonicalized to www.goodreads.com (never left
    as bare goodreads.com), so downstream fetches always target one
    consistent, known-good host.
    """
    raw = (raw or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="No Goodreads profile URL or user ID provided.")

    if raw.isdigit():
        if not _GOODREADS_USER_ID_RE.match(raw):
            raise HTTPException(
                status_code=400,
                detail="Goodreads user ID must be a positive number between 1 and 20 digits.",
            )
        return raw

    candidate = raw
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", candidate):
        candidate = f"https://{candidate}"

    parsed = urlsplit(candidate)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http(s) Goodreads URLs are supported.")

    host = (parsed.hostname or "").lower()
    if host not in GOODREADS_HOSTS:
        raise HTTPException(
            status_code=400,
            detail="Only goodreads.com profile URLs or a raw numeric Goodreads user ID are supported.",
        )

    path = parsed.path or "/"
    if not _ALLOWED_PROFILE_PATH_RE.match(path):
        raise HTTPException(
            status_code=400,
            detail="Only /user/show/... or /review/list/... Goodreads profile URLs are supported.",
        )

    # Always canonicalize to www.goodreads.com — never let a bare
    # goodreads.com input pass through unnormalized.
    # Queries are irrelevant for resolving the profile's numeric user ID and
    # can change Goodreads page behavior. Drop them from the canonical URL.
    return urlunsplit(("https", "www.goodreads.com", parsed.path, "", ""))


def _canonical_goodreads_url(raw_url: str) -> str:
    """Return a safe same-host Goodreads URL or reject the redirect target."""
    parsed = urlsplit(raw_url)
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "www.goodreads.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise HTTPException(
            status_code=400,
            detail="Goodreads redirected to an unexpected host or scheme.",
        )
    return urlunsplit(("https", "www.goodreads.com", parsed.path, parsed.query, ""))


async def _get_goodreads_same_host(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict,
    timeout: float,
) -> httpx.Response:
    """Follow a bounded redirect chain without ever leaving Goodreads."""
    current_url = _canonical_goodreads_url(url)
    for redirect_count in range(6):
        response = await client.get(
            current_url,
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        )
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        if redirect_count == 5:
            raise HTTPException(status_code=400, detail="Goodreads redirected too many times.")
        location = response.headers.get("location")
        if not location:
            raise HTTPException(status_code=400, detail="Goodreads returned a redirect without a destination.")
        current_url = _canonical_goodreads_url(urljoin(current_url, location))
    raise HTTPException(status_code=400, detail="Goodreads redirected too many times.")


async def resolve_numeric_user_id(profile_url: str, client: httpx.AsyncClient) -> str:
    """Extract numeric Goodreads user ID from a validated profile URL.

    Uses the same bounded digit pattern as validate_and_normalize_profile_input
    (1-20 digits, no leading zero) rather than an arbitrary minimum digit
    count — early Goodreads accounts can have short numeric IDs.
    """
    match = re.search(rf'/({_USER_ID_DIGITS})', profile_url)
    if match:
        return match.group(1)

    headers = {"User-Agent": "Mozilla/5.0 (compatible; ReadingDNA/1.0)"}
    try:
        resp = await _get_goodreads_same_host(client, profile_url, headers=headers, timeout=10)
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not reach Goodreads profile page ({safe_exception_summary(e)}).",
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Goodreads profile returned HTTP {resp.status_code}.")

    patterns = [
        rf'goodreads\.com/user/show/({_USER_ID_DIGITS})',
        rf'goodreads\.com/review/list/({_USER_ID_DIGITS})',
        rf'"user_id"\s*:\s*({_USER_ID_DIGITS})',
        rf'\/({_USER_ID_DIGITS})-',
    ]
    for pat in patterns:
        m = re.search(pat, resp.text)
        if m:
            return m.group(1)

    raise HTTPException(
        status_code=400,
        detail=(
            "Could not find your Goodreads user ID. "
            "Try using your full profile URL: goodreads.com/user/show/YOUR_ID or make sure your profile is public."
        ),
    )


def _extract_book_id_from_text(text: str) -> str:
    """Fallback: pull a numeric book id out of a /book/show/<id> style URL
    (used for the `link`/`guid` tags) when a dedicated book_id tag is
    missing or empty."""
    match = re.search(r"/book/show/(\d+)", text or "")
    return match.group(1) if match else ""


def parse_feed(xml_text: str, shelf: str) -> list[dict]:
    """Parse a Goodreads RSS feed. Returns all books; caller decides what to keep.

    Live schema confirmation: Goodreads' per-item fields (book_id,
    average_rating, user_read_at, user_date_added, user_shelves, etc.) are
    un-namespaced plain tags. `user_read_at` (not `user_date_read`) is the
    actual read-date field. `book_id` and the standard RSS `guid` tag are
    stable per-book/per-entry identifiers and are captured here so callers
    can dedupe across paginated requests by identity rather than title,
    which can collide for different editions/books that share a title.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    # Auto-detect Goodreads XML namespace (defensive fallback — the live
    # feed's item-level fields are confirmed un-namespaced, so this loop
    # normally leaves ns == "" and gr(name) below just returns name as-is).
    ns = ""
    for elem in channel.iter():
        tag = elem.tag
        if tag.startswith("{") and "goodreads" in tag:
            ns = tag[1:tag.index("}")]
            break

    def gr(name: str) -> str:
        return f"{{{ns}}}{name}" if ns else name

    books = []
    for item in channel.findall("item"):
        title = item.findtext("title", "").strip()
        link = (item.findtext("link", "") or "").strip()
        guid = (item.findtext("guid", "") or "").strip()
        author = item.findtext(gr("author_name"), "").strip()
        isbn = item.findtext(gr("isbn"), "").strip()
        pub_year = (item.findtext(gr("book_published"), "") or "").strip()
        rating_text = (item.findtext(gr("user_rating"), "") or "").strip()
        rating = int(rating_text) if rating_text.isdigit() else 0

        avg_rating_text = (item.findtext(gr("average_rating"), "") or "").strip()
        try:
            avg_rating = float(avg_rating_text) if avg_rating_text else 0.0
        except ValueError:
            avg_rating = 0.0

        num_pages_text = (item.findtext(gr("num_pages"), "") or "").strip()
        num_pages = int(num_pages_text) if num_pages_text.isdigit() else 0

        book_id = (item.findtext(gr("book_id"), "") or "").strip()
        if not book_id:
            book_id = _extract_book_id_from_text(guid) or _extract_book_id_from_text(link)

        # user_read_at is the confirmed live field name for the date a book
        # was marked read (previously assumed "user_date_read", which does
        # not exist in the real feed and left year_read unpopulated).
        date_read_text = (item.findtext(gr("user_read_at"), "") or "").strip()
        # Goodreads RSS formats this as RFC-822-ish, e.g. "Wed, 01 Jan 2020
        # 00:00:00 -0800" — pull the 4-digit year out for recency sorting.
        year_read_match = re.search(r"\b(\d{4})\b", date_read_text)
        year_read = int(year_read_match.group(1)) if year_read_match else None

        # Parse shelf metadata — lets us detect DNF books inside any shelf response
        exclusive_shelf = (item.findtext(gr("exclusive_shelf"), "") or "").strip()
        user_shelves_raw = (item.findtext(gr("user_shelves"), "") or "").strip()
        user_shelves = [s.strip().lower() for s in user_shelves_raw.split(",") if s.strip()]

        # Review text — strip HTML tags
        review_raw = (item.findtext(gr("user_review"), "") or "").strip()
        review_text = re.sub(r"<[^>]+>", " ", review_raw)
        review_text = re.sub(r"\s+", " ", review_text).strip()[:MAX_REVIEW_EXCERPT_CHARS]

        books.append({
            "title": title,
            "author": author,
            "isbn": isbn,
            "my_rating": rating,
            "avg_rating": round(max(0.0, min(5.0, avg_rating)), 2),
            "num_pages": max(0, num_pages),
            "year_published": pub_year,
            "date_read": date_read_text,
            "year_read": year_read,
            "shelves": exclusive_shelf or shelf,
            "my_review": review_text,
            "genres": [],
            # internal classification/identity helpers (stripped before returning)
            "_exclusive_shelf": exclusive_shelf,
            "_user_shelves": user_shelves,
            "_book_id": book_id,
            "_guid": guid,
        })
    return books


def _is_rss(text: str) -> bool:
    return bool(text) and "<rss" in text[:400]


def _book_key(b: dict) -> tuple:
    """Deterministic dedupe key.

    Prefers a stable Goodreads identity field — book_id, then the RSS guid —
    when available (RSS-sourced books only). These are per-book/per-entry
    identifiers and avoid false-negative dedupe misses for same-titled
    books/editions, which plain title/author matching cannot distinguish.
    Falls back to ISBN (works for CSV-sourced books too, which have no
    book_id/guid), then normalized (title, author) as a last resort.
    """
    book_id = (b.get("_book_id") or "").strip()
    if book_id:
        return ("book_id", book_id)
    guid = (b.get("_guid") or "").strip()
    if guid:
        return ("guid", guid)
    isbn = (b.get("isbn") or "").strip()
    if isbn:
        return ("isbn", isbn)
    return ("ta", (b.get("title") or "").strip().lower(), (b.get("author") or "").strip().lower())


def dedupe_books(books: list[dict]) -> list[dict]:
    """Deterministic, order-preserving de-duplication (first occurrence wins)."""
    seen: set = set()
    out = []
    for b in books:
        key = _book_key(b)
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def _strip_internal(b: dict) -> dict:
    """Remove _-prefixed internal keys before returning to caller."""
    return {k: v for k, v in b.items() if not k.startswith("_")}


async def _fetch_shelf_paginated(
    client: httpx.AsyncClient,
    user_id: str,
    shelf: str,
    headers: dict,
    warnings: list[str],
) -> tuple[list[dict], bool]:
    """Fetch every page of a shelf until exhaustion, a repeated page, or the
    configured page cap. Returns (books, primary_shelf_failed).

    primary_shelf_failed is True only when the very first page could not be
    loaded at all (malformed / non-200) — callers use this to distinguish a
    hard failure (e.g. the required 'read' shelf) from a shelf that simply
    returned nothing meaningful (e.g. no DNF books for this reader).
    """
    base = f"https://www.goodreads.com/review/list_rss/{user_id}"
    all_books: list[dict] = []
    seen_page_fingerprints: set = set()
    page = 1

    while page <= GOODREADS_RSS_MAX_PAGES:
        url = f"{base}?shelf={shelf}&per_page={GOODREADS_RSS_PER_PAGE}&page={page}"
        try:
            resp = await _get_goodreads_same_host(client, url, headers=headers, timeout=20)
        except HTTPException as e:
            if page == 1:
                return [], True
            warnings.append(
                f"Unsafe redirect paginating '{shelf}' shelf at page {page} ({e.detail}); returning partial data."
            )
            break
        except httpx.HTTPError as e:
            if page == 1:
                return [], True
            warnings.append(
                f"Network error paginating '{shelf}' shelf at page {page} "
                f"({safe_exception_summary(e)}); returning partial data."
            )
            break

        if resp.status_code != 200 or not _is_rss(resp.text):
            if page == 1:
                return [], True
            warnings.append(
                f"Stopped paginating '{shelf}' shelf at page {page}: malformed response (HTTP {resp.status_code})."
            )
            break

        page_books = parse_feed(resp.text, shelf)
        if not page_books:
            break  # shelf exhausted

        fingerprint = tuple(_book_key(b) for b in page_books)
        if fingerprint in seen_page_fingerprints:
            warnings.append(f"Detected a repeated page while paginating '{shelf}' shelf at page {page}; stopping.")
            break
        seen_page_fingerprints.add(fingerprint)

        # A blank title can't satisfy the (required, non-blank) Book model
        # downstream — filter such entries out here (with a visible warning)
        # rather than letting a legitimate import 422 later. Pagination
        # control above (fingerprint/short-page detection) intentionally
        # uses the raw, unfiltered page_books, since Goodreads' actual page
        # size should still govern those checks.
        blank_titled = [b for b in page_books if not (b.get("title") or "").strip()]
        if blank_titled:
            warnings.append(
                f"Skipped {len(blank_titled)} item(s) on the '{shelf}' shelf with a blank title."
            )
        all_books.extend(b for b in page_books if (b.get("title") or "").strip())

        if len(page_books) < GOODREADS_RSS_PER_PAGE:
            break  # short page == last page

        page += 1
        if page <= GOODREADS_RSS_MAX_PAGES:
            await asyncio.sleep(GOODREADS_RSS_PAGE_DELAY_SECONDS)
    else:
        warnings.append(
            f"Reached the configured page cap ({GOODREADS_RSS_MAX_PAGES} pages, "
            f"{GOODREADS_RSS_MAX_PAGES * GOODREADS_RSS_PER_PAGE} books) while paginating '{shelf}' shelf; "
            "some books may be missing."
        )

    return all_books, False


async def parse_rss(profile_url: str) -> dict:
    """Fetch and classify a reader's Goodreads shelves.

    Returns books/currently_reading/dnf/want_to_read/warnings/shelf_counts.
    `warnings` surfaces partial-data situations (page caps, malformed pages,
    non-primary shelf failures) instead of silently returning incomplete data.
    """
    normalized = validate_and_normalize_profile_input(profile_url)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ReadingDNA/1.0)"}
    warnings: list[str] = []

    async with httpx.AsyncClient(timeout=20) as client:
        user_id = normalized if normalized.isdigit() else await resolve_numeric_user_id(normalized, client)

        # Sequential requests with a polite delay — parallel hits Goodreads rate limits
        # and silently returns empty feeds.
        read_raw, read_failed = await _fetch_shelf_paginated(client, user_id, "read", headers, warnings)
        if read_failed:
            raise HTTPException(
                status_code=400,
                detail="Could not load your Goodreads 'read' shelf. Make sure your profile and shelves are set to Public.",
            )

        await asyncio.sleep(GOODREADS_RSS_PAGE_DELAY_SECONDS)
        reading_raw, reading_failed = await _fetch_shelf_paginated(client, user_id, "currently-reading", headers, warnings)
        if reading_failed:
            warnings.append("Could not load the 'currently-reading' shelf; continuing without it.")
            reading_raw = []

        await asyncio.sleep(GOODREADS_RSS_PAGE_DELAY_SECONDS)
        dnf_raw, dnf_failed = await _fetch_shelf_paginated(client, user_id, "did-not-finish", headers, warnings)
        if dnf_failed:
            warnings.append(
                "Could not load the dedicated 'did-not-finish' shelf; using DNF-style shelf tags found on the read shelf instead."
            )
            dnf_raw = []

        await asyncio.sleep(GOODREADS_RSS_PAGE_DELAY_SECONDS)
        tbr_raw, tbr_failed = await _fetch_shelf_paginated(client, user_id, "to-read", headers, warnings)
        if tbr_failed:
            warnings.append("Could not load the 'to-read' shelf; continuing without it.")
            tbr_raw = []

    # Dedupe first (while _book_id/_guid/_user_shelves are still present, so
    # cross-page dedupe can use the stable Goodreads identity fields instead
    # of falling back to title/author) — internal fields are stripped only
    # once, right before each list is returned to the caller.
    read_raw = dedupe_books(read_raw)
    reading_deduped = dedupe_books(reading_raw)
    dnf_dedicated_deduped = dedupe_books(dnf_raw)

    # DNF: combine the dedicated shelf feed WITH DNF-style tags found in the read feed
    # (merged whenever both are present, not a fallback used only if the dedicated feed fails).
    dnf_from_read_tags = [
        b for b in read_raw
        if any(s in DNF_SHELF_KEYWORDS for s in b.get("_user_shelves", []))
    ]
    dnf_deduped = dedupe_books(dnf_dedicated_deduped + dnf_from_read_tags)

    tbr_deduped = dedupe_books(tbr_raw)

    dnf_keys = {_book_key(b) for b in dnf_deduped}
    books_deduped = dedupe_books([
        b for b in read_raw
        if b["my_rating"] > 0 and _book_key(b) not in dnf_keys
    ])

    # Strip internal helper fields (_exclusive_shelf, _user_shelves, _book_id,
    # _guid) only now that all identity-based dedupe/filtering is done.
    currently_reading = [_strip_internal(b) for b in reading_deduped]
    dnf = [_strip_internal(b) for b in dnf_deduped]
    want_to_read = [_strip_internal(b) for b in tbr_deduped]
    books = [_strip_internal(b) for b in books_deduped]

    shelf_counts = {
        "read": len(books),
        "currently_reading": len(currently_reading),
        "dnf": len(dnf),
        "want_to_read": len(want_to_read),
    }

    return {
        "books": books,
        "currently_reading": currently_reading,
        "dnf": dnf,
        "want_to_read": want_to_read,
        "warnings": warnings,
        "shelf_counts": shelf_counts,
    }
