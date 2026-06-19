import httpx
import asyncio
from typing import Optional

OVERDRIVE_SEARCH = "https://thunder.api.overdrive.com/v2/libraries/{library_key}/media"
LIBRARY_SEARCH = "https://thunder.api.overdrive.com/v2/libraries"


async def find_library_key(library_name: str) -> Optional[str]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            LIBRARY_SEARCH,
            params={"query": library_name, "perPage": 5},
            headers={"User-Agent": "ReadingDNA/1.0"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        items = data.get("items", [])
        if items:
            return items[0].get("advantageKey") or items[0].get("websiteId")
    return None


async def check_isbn(client: httpx.AsyncClient, isbn: str, library_key: str) -> dict:
    if not isbn or not library_key:
        return {"isbn": isbn, "status": "unknown", "available": False, "wait_weeks": None}
    try:
        resp = await client.get(
            OVERDRIVE_SEARCH.format(library_key=library_key),
            params={"query": isbn, "perPage": 1, "format": "ebook-epub-adobe"},
            headers={"User-Agent": "ReadingDNA/1.0"},
            timeout=8,
        )
        if resp.status_code != 200:
            return {"isbn": isbn, "status": "not_found", "available": False, "wait_weeks": None}
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return {"isbn": isbn, "status": "not_in_catalog", "available": False, "wait_weeks": None}
        item = items[0]
        availability = item.get("availability", {})
        copies_available = availability.get("copiesAvailable", 0)
        copies_owned = availability.get("copiesOwned", 0)
        holds = availability.get("numberOfHolds", 0)
        if copies_available > 0:
            return {"isbn": isbn, "status": "available", "available": True, "wait_weeks": 0}
        elif copies_owned > 0:
            wait = max(1, round(holds / max(copies_owned, 1) * 3))
            return {"isbn": isbn, "status": "waitlist", "available": False, "wait_weeks": wait}
        else:
            return {"isbn": isbn, "status": "not_in_catalog", "available": False, "wait_weeks": None}
    except Exception as e:
        return {"isbn": isbn, "status": "error", "available": False, "wait_weeks": None, "error": str(e)}


async def check_availability(isbns: list[str], library_name: str) -> dict:
    library_key = await find_library_key(library_name)
    if not library_key:
        return {
            "library_found": False,
            "library_name": library_name,
            "results": {isbn: {"status": "library_not_found", "available": False} for isbn in isbns},
        }

    async with httpx.AsyncClient() as client:
        tasks = [check_isbn(client, isbn, library_key) for isbn in isbns]
        results = await asyncio.gather(*tasks)

    return {
        "library_found": True,
        "library_name": library_name,
        "library_key": library_key,
        "results": {r["isbn"]: r for r in results},
    }
