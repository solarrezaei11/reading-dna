"""Tests for libby.py: key/name/URL matching, ISBN validation, and the
/libby response shape. Media/catalog network calls are mocked so no real
OverDrive/Libby traffic is made.
"""
import asyncio
import unittest
from unittest import mock

import httpx

import libby

SAMPLE_CATALOG = [
    {"name": "Multnomah County Library", "preferredKey": "multcolib"},
    {"name": "Seattle Public Library", "preferredKey": "spl"},
    {"name": "King County Library System", "preferredKey": "kcls"},
]


class IsbnValidationTests(unittest.TestCase):
    def test_valid_isbn13(self):
        self.assertTrue(libby.is_valid_isbn("9780000000001"))

    def test_valid_isbn10_with_x_check_digit(self):
        self.assertTrue(libby.is_valid_isbn("123456789X"))

    def test_valid_isbn_with_hyphens(self):
        self.assertTrue(libby.is_valid_isbn("978-0-00-000000-1"))

    def test_invalid_isbn_too_short(self):
        self.assertFalse(libby.is_valid_isbn("12345"))

    def test_invalid_isbn_non_numeric(self):
        self.assertFalse(libby.is_valid_isbn("not-an-isbn"))

    def test_empty_isbn(self):
        self.assertFalse(libby.is_valid_isbn(""))


class ParseLibraryUrlOrKeyTests(unittest.TestCase):
    def test_libbyapp_library_path(self):
        self.assertEqual(libby.parse_library_url_or_key("https://libbyapp.com/library/multcolib"), "multcolib")

    def test_libbyapp_library_path_with_trailing_segments(self):
        self.assertEqual(libby.parse_library_url_or_key("https://libbyapp.com/library/multcolib/search"), "multcolib")

    def test_overdrive_subdomain_url(self):
        self.assertEqual(libby.parse_library_url_or_key("https://multcolib.overdrive.com/some/path"), "multcolib")

    def test_thunder_api_host_not_treated_as_library_subdomain(self):
        self.assertIsNone(libby.parse_library_url_or_key("https://thunder.api.overdrive.com/v2/libraries"))

    def test_lookalike_libbyapp_host_is_rejected(self):
        # "evillibbyapp.com" ends with the substring "libbyapp.com" but is
        # NOT libbyapp.com or a subdomain of it — must not be treated as
        # a trusted Libby host.
        self.assertIsNone(libby.parse_library_url_or_key("https://evillibbyapp.com/library/multcolib"))

    def test_libbyapp_subdomain_is_accepted(self):
        self.assertEqual(
            libby.parse_library_url_or_key("https://foo.libbyapp.com/library/multcolib"), "multcolib"
        )

    def test_bare_key_token(self):
        self.assertEqual(libby.parse_library_url_or_key("multcolib"), "multcolib")

    def test_plain_library_name_is_not_a_key(self):
        self.assertIsNone(libby.parse_library_url_or_key("Multnomah County Library"))

    def test_empty_input(self):
        self.assertIsNone(libby.parse_library_url_or_key(""))

    def test_path_injection_key_is_rejected(self):
        self.assertIsNone(libby.parse_library_url_or_key("https://libbyapp.com/library/%2e%2e%2fmedia"))


class MatchLibraryTests(unittest.TestCase):
    def test_exact_match(self):
        match, alts = libby.match_library(SAMPLE_CATALOG, "Seattle Public Library")
        self.assertIsNotNone(match)
        self.assertEqual(match["preferredKey"], "spl")
        self.assertEqual(alts, [])

    def test_normalized_match_ignores_stopwords_and_case(self):
        match, alts = libby.match_library(SAMPLE_CATALOG, "seattle public")
        self.assertIsNotNone(match)
        self.assertEqual(match["preferredKey"], "spl")

    def test_fuzzy_match_handles_typo(self):
        match, alts = libby.match_library(SAMPLE_CATALOG, "Multnomah Cnty Library")
        self.assertIsNotNone(match)
        self.assertEqual(match["preferredKey"], "multcolib")

    def test_unicode_name_matching_folds_diacritics(self):
        catalog = [{"name": "Bibliothèque de Montréal", "preferredKey": "montreal"}]
        match, alts = libby.match_library(catalog, "Bibliotheque de Montreal")
        self.assertIsNotNone(match)
        self.assertEqual(match["preferredKey"], "montreal")

    def test_ambiguous_or_unrelated_query_does_not_silently_pick_first(self):
        match, alts = libby.match_library(SAMPLE_CATALOG, "Some Totally Unrelated Town Library")
        self.assertIsNone(match)

    def test_empty_query_returns_no_match(self):
        match, alts = libby.match_library(SAMPLE_CATALOG, "")
        self.assertIsNone(match)
        self.assertEqual(alts, [])


class CheckAvailabilityShapeTests(unittest.IsolatedAsyncioTestCase):
    """check_availability's response shape must always be:
    {library_found, library_name, matched_library_name, library_key, alternatives, results}
    with results keyed by ISBN.

    Live API findings this exercises: media search results carry no real
    availability (must be fetched from the dedicated
    /media/{titleId}/availability endpoint), and direct URL/key lookups use
    GET /v2/libraries/{key} instead of paginating the full catalog.
    """

    REQUIRED_KEYS = {"library_found", "library_name", "matched_library_name", "library_key", "alternatives", "results", "warnings"}

    @staticmethod
    def _routed_handler(library_detail=None, search_items=None, availability=None, availability_status=200):
        """Route mocked responses by URL shape:
        .../v2/libraries/<key>                      -> library_detail (direct lookup)
        .../v2/libraries/<key>/media                 -> {"items": search_items}
        .../v2/libraries/<key>/media/<id>/availability -> availability payload
        """
        def handler(request: httpx.Request) -> httpx.Response:
            path_parts = [p for p in request.url.path.split("/") if p]
            if path_parts and path_parts[-1] == "availability":
                return httpx.Response(availability_status, json=availability or {})
            if path_parts and path_parts[-1] == "media":
                return httpx.Response(200, json={"items": search_items if search_items is not None else []})
            if library_detail is not None:
                return httpx.Response(200, json=library_detail)
            return httpx.Response(404, json={})
        return handler

    def _patched(self, handler):
        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient

        def _factory(*args, **kwargs):
            return real_async_client(transport=transport)

        return mock.patch("httpx.AsyncClient", side_effect=_factory)

    async def test_library_found_uses_preferred_key_not_website_id(self):
        handler = self._routed_handler(
            search_items=[{"id": "title-123", "isbn": "9780000000001", "isbns": ["9780000000001"]}],
            availability={"isAvailable": True, "availableCopies": 2, "ownedCopies": 3, "holdsCount": 0},
        )
        with mock.patch.object(libby, "load_catalog", new=mock.AsyncMock(return_value=(SAMPLE_CATALOG, []))), \
             self._patched(handler):
            result = await libby.check_availability(["9780000000001"], "Seattle Public Library")

        self.assertEqual(set(result.keys()), self.REQUIRED_KEYS)
        self.assertTrue(result["library_found"])
        self.assertEqual(result["matched_library_name"], "Seattle Public Library")
        self.assertEqual(result["library_key"], "spl")  # preferredKey, never websiteId
        self.assertIsInstance(result["results"], dict)
        self.assertEqual(result["results"]["9780000000001"]["status"], "available")

    async def test_availability_uses_dedicated_endpoint_not_search_item_field(self):
        # Search item deliberately carries no availability info (matches the
        # live API — search results' `availability` field is null); only the
        # dedicated endpoint's isAvailable/ownedCopies/holdsCount are trusted.
        handler = self._routed_handler(
            search_items=[{"id": "title-999", "isbn": "9780000000002", "isbns": ["9780000000002"], "availability": None}],
            availability={"isAvailable": False, "ownedCopies": 2, "holdsCount": 4, "estimatedWaitDays": 21},
        )
        with mock.patch.object(libby, "load_catalog", new=mock.AsyncMock(return_value=(SAMPLE_CATALOG, []))), \
             self._patched(handler):
            result = await libby.check_availability(["9780000000002"], "Seattle Public Library")

        entry = result["results"]["9780000000002"]
        self.assertEqual(entry["status"], "waitlist")
        self.assertEqual(entry["wait_weeks"], 3)  # round(21 days / 7)

    async def test_availability_404_means_not_in_catalog_for_this_library(self):
        handler = self._routed_handler(
            search_items=[{"id": "title-1", "isbn": "9780000000003", "isbns": ["9780000000003"]}],
            availability_status=404,
        )
        with mock.patch.object(libby, "load_catalog", new=mock.AsyncMock(return_value=(SAMPLE_CATALOG, []))), \
             self._patched(handler):
            result = await libby.check_availability(["9780000000003"], "Seattle Public Library")

        self.assertEqual(result["results"]["9780000000003"]["status"], "not_in_catalog")

    async def test_library_not_found_returns_alternatives_and_result_shape(self):
        with mock.patch.object(libby, "load_catalog", new=mock.AsyncMock(return_value=(SAMPLE_CATALOG, []))):
            result = await libby.check_availability(["9780000000001"], "Totally Unrelated Town Library")

        self.assertEqual(set(result.keys()), self.REQUIRED_KEYS)
        self.assertFalse(result["library_found"])
        self.assertIsNone(result["matched_library_name"])
        self.assertIsNone(result["library_key"])
        self.assertEqual(result["results"]["9780000000001"]["status"], "library_not_found")

    async def test_invalid_isbn_short_circuits_without_network_call(self):
        network_called = {"flag": False}

        def handler(request: httpx.Request) -> httpx.Response:
            network_called["flag"] = True
            return httpx.Response(200, json={"items": []})

        with mock.patch.object(libby, "load_catalog", new=mock.AsyncMock(return_value=(SAMPLE_CATALOG, []))), \
             self._patched(handler):
            result = await libby.check_availability(["not-an-isbn"], "Seattle Public Library")

        self.assertEqual(result["results"]["not-an-isbn"]["status"], "invalid_isbn")
        self.assertFalse(network_called["flag"])

    async def test_direct_key_resolves_via_single_library_get_not_full_catalog(self):
        # "kcls" parses as a bare key candidate -> should hit
        # GET /v2/libraries/kcls directly and never need the paginated catalog.
        handler = self._routed_handler(
            library_detail={"preferredKey": "kcls", "name": "King County Library System"},
            search_items=[{"id": "title-1", "isbn": "9780000000001", "isbns": ["9780000000001"]}],
            availability={"isAvailable": True, "ownedCopies": 5, "holdsCount": 0},
        )
        catalog_mock = mock.AsyncMock(return_value=(SAMPLE_CATALOG, []))
        with mock.patch.object(libby, "load_catalog", new=catalog_mock), \
             self._patched(handler):
            result = await libby.check_availability(["9780000000001"], "kcls")

        self.assertTrue(result["library_found"])
        self.assertEqual(result["library_key"], "kcls")
        self.assertEqual(result["matched_library_name"], "King County Library System")
        catalog_mock.assert_not_called()  # fast path bypassed the full catalog fetch entirely

    async def test_direct_key_falls_back_to_catalog_when_direct_lookup_fails(self):
        # If GET /v2/libraries/<key> 404s (e.g. the "key" guess was wrong),
        # resolution must still fall back to catalog-based matching rather
        # than reporting library_not_found outright.
        def handler(request: httpx.Request) -> httpx.Response:
            path_parts = [p for p in request.url.path.split("/") if p]
            if path_parts and path_parts[-1] == "kcls":
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"items": []})

        with mock.patch.object(libby, "load_catalog", new=mock.AsyncMock(return_value=(SAMPLE_CATALOG, []))), \
             self._patched(handler):
            result = await libby.check_availability(["9780000000001"], "kcls")

        self.assertTrue(result["library_found"])
        self.assertEqual(result["library_key"], "kcls")

    async def test_malformed_media_items_is_an_error_not_a_false_no_match(self):
        handler = self._routed_handler(search_items="not-a-list")
        with mock.patch.object(libby, "load_catalog", new=mock.AsyncMock(return_value=(SAMPLE_CATALOG, []))), \
             self._patched(handler):
            result = await libby.check_availability(["9780000000001"], "Seattle Public Library")

        entry = result["results"]["9780000000001"]
        self.assertEqual(entry["status"], "error")
        self.assertIn("items", entry["error"])

    async def test_string_boolean_availability_is_rejected(self):
        handler = self._routed_handler(
            search_items=[{"id": "title-1", "isbns": ["9780000000001"]}],
            availability={"isAvailable": "false", "ownedCopies": 1, "holdsCount": 0},
        )
        with mock.patch.object(libby, "load_catalog", new=mock.AsyncMock(return_value=(SAMPLE_CATALOG, []))), \
             self._patched(handler):
            result = await libby.check_availability(["9780000000001"], "Seattle Public Library")

        self.assertEqual(result["results"]["9780000000001"]["status"], "error")

    async def test_waitlist_without_real_estimate_does_not_invent_one(self):
        handler = self._routed_handler(
            search_items=[{"id": "title-1", "isbns": ["9780000000001"]}],
            availability={"isAvailable": False, "ownedCopies": 2, "holdsCount": 8},
        )
        with mock.patch.object(libby, "load_catalog", new=mock.AsyncMock(return_value=(SAMPLE_CATALOG, []))), \
             self._patched(handler):
            result = await libby.check_availability(["9780000000001"], "Seattle Public Library")

        entry = result["results"]["9780000000001"]
        self.assertEqual(entry["status"], "waitlist")
        self.assertIsNone(entry["wait_weeks"])
        self.assertEqual(entry["holds_count"], 8)


class LoadCatalogPaginationTests(unittest.IsolatedAsyncioTestCase):
    """load_catalog fetches page 1 alone (to learn totalItems), then the
    remaining pages CONCURRENTLY, bounded by LIBBY_CATALOG_FETCH_CONCURRENCY,
    while preserving deterministic page ordering and surfacing per-page
    failures as warnings rather than aborting the whole fetch.

    LIBBY_CATALOG_PER_PAGE is patched to 1 in these tests so a single mock
    item per page counts as a "full" page — this lets page-count math
    (total_pages = ceil(total_items / per_page)) work with tiny, readable
    fixtures instead of needing to fabricate 100 items per page.
    """

    def setUp(self):
        # Each test needs a fresh, empty cache so load_catalog actually fetches.
        libby._catalog_cache = libby._CatalogCache()

    async def test_malformed_catalog_items_field_is_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"items": {"not": "a list"}, "totalItems": 1})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ValueError):
                await libby._fetch_catalog_page(client, 1)

    @staticmethod
    def _page_items(page: int, n: int = 1) -> list[dict]:
        return [{"preferredKey": f"lib-p{page}-{i}", "name": f"Library page {page} #{i}"} for i in range(n)]

    async def test_remaining_pages_fetched_concurrently_within_bound(self):
        total_pages = 12
        concurrency_limit = 3
        in_flight = {"current": 0, "max": 0}
        lock = asyncio.Lock()

        async def fake_fetch(client, page):
            if page == 1:
                # Page 1 alone, sequential — reports total so the rest can
                # be computed and fetched concurrently.
                return self._page_items(1), total_pages
            async with lock:
                in_flight["current"] += 1
                in_flight["max"] = max(in_flight["max"], in_flight["current"])
            try:
                await asyncio.sleep(0.02)
                return self._page_items(page), None
            finally:
                async with lock:
                    in_flight["current"] -= 1

        with mock.patch.object(libby, "LIBBY_CATALOG_PER_PAGE", 1), \
             mock.patch.object(libby, "LIBBY_CATALOG_FETCH_CONCURRENCY", concurrency_limit), \
             mock.patch.object(libby, "_fetch_catalog_page", side_effect=fake_fetch):
            libraries, warnings = await libby.load_catalog(force_refresh=True)

        self.assertEqual(len(libraries), total_pages)  # one item per page, 1..total_pages
        self.assertLessEqual(in_flight["max"], concurrency_limit)
        self.assertEqual(warnings, [])

    async def test_pages_are_concatenated_in_deterministic_page_order(self):
        total_pages = 6

        async def fake_fetch(client, page):
            if page == 1:
                return self._page_items(1), total_pages
            # Earlier pages sleep longer than later pages, so if results were
            # simply appended in completion order (rather than re-sorted by
            # page number), later pages would appear first.
            await asyncio.sleep(0.01 * (total_pages - page))
            return self._page_items(page), None

        with mock.patch.object(libby, "LIBBY_CATALOG_PER_PAGE", 1), \
             mock.patch.object(libby, "_fetch_catalog_page", side_effect=fake_fetch):
            libraries, warnings = await libby.load_catalog(force_refresh=True)

        self.assertEqual(
            [lib["preferredKey"] for lib in libraries],
            [f"lib-p{p}-0" for p in range(1, total_pages + 1)],
        )
        self.assertEqual(warnings, [])

    async def test_partial_page_failure_is_recorded_as_warning_not_aborted(self):
        total_pages = 5
        failing_page = 3

        async def fake_fetch(client, page):
            if page == 1:
                return self._page_items(1), total_pages
            if page == failing_page:
                raise httpx.HTTPError(f"simulated failure on page {page}")
            return self._page_items(page), None

        with mock.patch.object(libby, "LIBBY_CATALOG_PER_PAGE", 1), \
             mock.patch.object(libby, "_fetch_catalog_page", side_effect=fake_fetch):
            libraries, warnings = await libby.load_catalog(force_refresh=True)

        fetched_pages = {p for p in range(1, total_pages + 1) if p != failing_page}
        self.assertEqual(
            {lib["preferredKey"] for lib in libraries},
            {f"lib-p{p}-0" for p in fetched_pages},
        )
        self.assertTrue(any(str(failing_page) in w for w in warnings))

    async def test_missing_total_items_falls_back_to_sequential_fetch(self):
        # No totalItems on page 1 -> can't safely compute page count, so the
        # sequential stop-on-short-page fallback is used instead.
        pages_seen: list[int] = []

        async def fake_fetch(client, page):
            pages_seen.append(page)
            if page <= 3:
                return self._page_items(page), None  # total_items intentionally omitted
            return [], None  # short/empty page stops the sequential loop

        with mock.patch.object(libby, "LIBBY_CATALOG_PER_PAGE", 1), \
             mock.patch.object(libby, "_fetch_catalog_page", side_effect=fake_fetch):
            libraries, warnings = await libby.load_catalog(force_refresh=True)

        self.assertEqual(
            [lib["preferredKey"] for lib in libraries],
            [f"lib-p{p}-0" for p in range(1, 4)],
        )
        self.assertEqual(pages_seen, [1, 2, 3, 4])
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
