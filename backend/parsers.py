import asyncio
import csv
import io
import re
import httpx
import xml.etree.ElementTree as ET
from fastapi import HTTPException


def parse_csv(content: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(content))
    books = []
    for row in reader:
        rating = row.get("My Rating", "0").strip()
        if not rating or rating == "0":
            continue
        date_read = row.get("Date Read", "").strip()
        year_read = None
        if date_read:
            try:
                year_read = int(date_read.split("/")[-1]) if "/" in date_read else int(date_read[:4])
            except Exception:
                pass

        books.append({
            "title": row.get("Title", "").strip(),
            "author": row.get("Author", "").strip(),
            "isbn": row.get("ISBN13", row.get("ISBN", "")).strip().replace("=", "").replace('"', ""),
            "my_rating": int(rating),
            "avg_rating": float(row.get("Average Rating", "0") or 0),
            "num_pages": int(row.get("Number of Pages", "0") or 0),
            "year_published": row.get("Original Publication Year", "").strip(),
            "date_read": date_read,
            "year_read": year_read,
            "shelves": row.get("Exclusive Shelf", "").strip(),
            "my_review": row.get("My Review", "").strip(),
            "genres": [],
        })
    return books


async def resolve_numeric_user_id(profile_url: str, client: httpx.AsyncClient) -> str:
    """Extract numeric Goodreads user ID from a profile URL, fetching the page if needed."""
    match = re.search(r'/(\d{5,})', profile_url)
    if match:
        return match.group(1)

    headers = {"User-Agent": "Mozilla/5.0 (compatible; ReadingDNA/1.0)"}
    try:
        resp = await client.get(profile_url, headers=headers, timeout=10, follow_redirects=True)
        patterns = [
            r'goodreads\.com/user/show/(\d+)',
            r'goodreads\.com/review/list/(\d+)',
            r'"user_id"\s*:\s*(\d+)',
            r'\/(\d{5,})-',
        ]
        for pat in patterns:
            m = re.search(pat, resp.text)
            if m:
                return m.group(1)
    except Exception:
        pass

    raise HTTPException(
        status_code=400,
        detail=(
            "Could not find your Goodreads user ID. "
            "Try using your full profile URL: goodreads.com/user/show/YOUR_ID or make sure your profile is public."
        ),
    )


def parse_feed(xml_text: str, shelf: str) -> list[dict]:
    """Parse a Goodreads RSS feed. Returns all books; caller decides what to keep."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    # Auto-detect Goodreads XML namespace
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
        author = item.findtext(gr("author_name"), "").strip()
        isbn = item.findtext(gr("isbn"), "").strip()
        pub_year = (item.findtext(gr("book_published"), "") or "").strip()
        rating_text = (item.findtext(gr("user_rating"), "") or "").strip()
        rating = int(rating_text) if rating_text.isdigit() else 0

        # Parse shelf metadata — lets us detect DNF books inside any shelf response
        exclusive_shelf = (item.findtext(gr("exclusive_shelf"), "") or "").strip()
        user_shelves_raw = (item.findtext(gr("user_shelves"), "") or "").strip()
        user_shelves = [s.strip().lower() for s in user_shelves_raw.split(",") if s.strip()]

        books.append({
            "title": title,
            "author": author,
            "isbn": isbn,
            "my_rating": rating,
            "avg_rating": 0,
            "num_pages": 0,
            "year_published": pub_year,
            "date_read": "",
            "year_read": None,
            "shelves": exclusive_shelf or shelf,
            "my_review": "",
            "genres": [],
            # internal classification helpers (stripped before returning)
            "_exclusive_shelf": exclusive_shelf,
            "_user_shelves": user_shelves,
        })
    return books


def _is_rss(text: str) -> bool:
    return bool(text) and "<rss" in text[:400]


async def parse_rss(profile_url: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ReadingDNA/1.0)"}

    async with httpx.AsyncClient(timeout=20) as client:
        user_id = await resolve_numeric_user_id(profile_url, client)
        base = f"https://www.goodreads.com/review/list_rss/{user_id}"

        # Sequential requests — parallel hits Goodreads rate limits and silently returns empty feeds
        read_resp = await client.get(
            f"{base}?shelf=read&per_page=200", headers=headers, follow_redirects=True
        )

        if read_resp.status_code != 200 or not _is_rss(read_resp.text):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Could not load your Goodreads 'read' shelf (HTTP {read_resp.status_code}). "
                    "Make sure your profile and shelves are set to Public."
                ),
            )

        await asyncio.sleep(0.4)
        reading_resp = await client.get(
            f"{base}?shelf=currently-reading&per_page=200", headers=headers, follow_redirects=True
        )

        await asyncio.sleep(0.4)
        dnf_resp = await client.get(
            f"{base}?shelf=did-not-finish&per_page=200", headers=headers, follow_redirects=True
        )

    # Parse all three feeds
    read_raw = parse_feed(read_resp.text, "read")

    currently_reading = (
        [_strip_internal(b) for b in parse_feed(reading_resp.text, "currently-reading")]
        if _is_rss(reading_resp.text)
        else []
    )

    # DNF: primary source is the dedicated shelf feed; fallback is user_shelves on the read feed
    if _is_rss(dnf_resp.text):
        dnf = [_strip_internal(b) for b in parse_feed(dnf_resp.text, "did-not-finish")]
    else:
        # Fallback: any book in the read feed tagged with a DNF-style custom shelf
        dnf_keywords = {"did-not-finish", "dnf", "did-not-finish", "abandoned", "gave-up"}
        dnf = [
            _strip_internal(b) for b in read_raw
            if any(s in dnf_keywords for s in b.get("_user_shelves", []))
        ]

    # Build the read list: rated books that aren't in DNF
    dnf_titles_lower = {b["title"].lower() for b in dnf}
    books = [
        _strip_internal(b) for b in read_raw
        if b["my_rating"] > 0 and b["title"].lower() not in dnf_titles_lower
    ]

    return {
        "books": books,
        "currently_reading": currently_reading,
        "dnf": dnf,
    }


def _strip_internal(b: dict) -> dict:
    """Remove _-prefixed internal keys before returning to caller."""
    return {k: v for k, v in b.items() if not k.startswith("_")}
