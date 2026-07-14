"""Tests for paginated Goodreads RSS shelf fetching, dedupe, and partial-data warnings.

Uses a fake httpx.AsyncClient (built from a MockTransport) so no real network
calls are made. GOODREADS_RSS_PER_PAGE / MAX_PAGES / PAGE_DELAY are patched
to small values so tests run fast and exercise pagination edges directly.
"""
import unittest
from unittest import mock

import httpx

import parsers


def _rss_xml(items: list[dict]) -> str:
    item_xml = "".join(
        f"""<item>
            <title>{it['title']}</title>
            <guid>{it.get('guid', '')}</guid>
            <book_id>{it.get('book_id', '')}</book_id>
            <author_name>{it.get('author', 'Author')}</author_name>
            <isbn>{it.get('isbn', '')}</isbn>
            <user_rating>{it.get('rating', 5)}</user_rating>
            <average_rating>{it.get('avg_rating', '4.10')}</average_rating>
            <user_read_at>{it.get('user_read_at', '')}</user_read_at>
            <exclusive_shelf>{it.get('shelf', 'read')}</exclusive_shelf>
            <user_shelves>{it.get('user_shelves', '')}</user_shelves>
        </item>"""
        for it in items
    )
    return f"<?xml version='1.0'?><rss><channel>{item_xml}</channel></rss>"


def _books(n: int, start: int = 0, **kwargs) -> list[dict]:
    return [{"title": f"Book {start + i}", "isbn": f"{9780000000000 + start + i}", **kwargs} for i in range(n)]


class FetchShelfPaginatedTests(unittest.IsolatedAsyncioTestCase):
    async def test_stops_on_short_page_no_warning(self):
        # Page 1 full (per_page items), page 2 short => natural exhaustion, no warning needed.
        pages = {
            1: _rss_xml(_books(3)),
            2: _rss_xml(_books(1, start=3)),
        }

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("page", "1"))
            return httpx.Response(200, text=pages.get(page, _rss_xml([])))

        with mock.patch.object(parsers, "GOODREADS_RSS_PER_PAGE", 3), \
             mock.patch.object(parsers, "GOODREADS_RSS_MAX_PAGES", 10), \
             mock.patch.object(parsers, "GOODREADS_RSS_PAGE_DELAY_SECONDS", 0):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                warnings: list[str] = []
                books, failed = await parsers._fetch_shelf_paginated(client, "1", "read", {}, warnings)

        self.assertFalse(failed)
        self.assertEqual(len(books), 4)
        self.assertEqual(warnings, [])

    async def test_dedupes_across_pages_when_goodreads_repeats_last_page(self):
        # Goodreads sometimes returns the same (final, short) page again when
        # asked for one page past the end — this must be detected and stopped,
        # not silently duplicated into the result set.
        page1 = _books(3)
        repeated_page = _books(2, start=3)  # a short page (< per_page) returned twice

        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("page", "1"))
            if page == 1:
                return httpx.Response(200, text=_rss_xml(page1))
            # Every page >= 2 returns the exact same short page (simulating a
            # server-side quirk) — this is a *repeat*, not a naturally short
            # final page, because it doesn't shrink or empty out.
            call_count["n"] += 1
            return httpx.Response(200, text=_rss_xml(repeated_page))

        with mock.patch.object(parsers, "GOODREADS_RSS_PER_PAGE", 3), \
             mock.patch.object(parsers, "GOODREADS_RSS_MAX_PAGES", 10), \
             mock.patch.object(parsers, "GOODREADS_RSS_PAGE_DELAY_SECONDS", 0):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                warnings: list[str] = []
                books, failed = await parsers._fetch_shelf_paginated(client, "1", "read", {}, warnings)

        self.assertFalse(failed)
        # Page 1 (3 books) + page 2 (2 books, short => stop naturally after
        # recording it once). Because it's short, pagination stops before a
        # repeat could even occur, so no repeat warning in this scenario —
        # this asserts total books stay at 5, not accumulate on retries.
        self.assertEqual(len(books), 5)

    async def test_repeated_full_page_triggers_dedupe_warning_and_stop(self):
        # A full-size page repeated verbatim (not shrinking) — server keeps
        # returning the same "last" full page instead of an empty one.
        same_page = _books(3)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_rss_xml(same_page))

        with mock.patch.object(parsers, "GOODREADS_RSS_PER_PAGE", 3), \
             mock.patch.object(parsers, "GOODREADS_RSS_MAX_PAGES", 10), \
             mock.patch.object(parsers, "GOODREADS_RSS_PAGE_DELAY_SECONDS", 0):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                warnings: list[str] = []
                books, failed = await parsers._fetch_shelf_paginated(client, "1", "read", {}, warnings)

        self.assertFalse(failed)
        self.assertEqual(len(books), 3)  # only the first occurrence is kept
        self.assertTrue(any("repeated page" in w for w in warnings))

    async def test_hits_configured_page_cap_and_warns(self):
        # Every page is full-size and unique — pagination could go on
        # forever without a cap.
        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("page", "1"))
            return httpx.Response(200, text=_rss_xml(_books(2, start=(page - 1) * 2)))

        with mock.patch.object(parsers, "GOODREADS_RSS_PER_PAGE", 2), \
             mock.patch.object(parsers, "GOODREADS_RSS_MAX_PAGES", 3), \
             mock.patch.object(parsers, "GOODREADS_RSS_PAGE_DELAY_SECONDS", 0):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                warnings: list[str] = []
                books, failed = await parsers._fetch_shelf_paginated(client, "1", "read", {}, warnings)

        self.assertFalse(failed)
        self.assertEqual(len(books), 6)  # 3 pages * 2 books/page, capped
        self.assertTrue(any("page cap" in w for w in warnings))

    async def test_page_one_failure_marks_primary_shelf_failed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        with mock.patch.object(parsers, "GOODREADS_RSS_PER_PAGE", 3), \
             mock.patch.object(parsers, "GOODREADS_RSS_MAX_PAGES", 5), \
             mock.patch.object(parsers, "GOODREADS_RSS_PAGE_DELAY_SECONDS", 0):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                warnings: list[str] = []
                books, failed = await parsers._fetch_shelf_paginated(client, "1", "did-not-finish", {}, warnings)

        self.assertTrue(failed)
        self.assertEqual(books, [])

    async def test_malformed_later_page_stops_with_partial_data_and_warning(self):
        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("page", "1"))
            if page == 1:
                return httpx.Response(200, text=_rss_xml(_books(3)))
            return httpx.Response(500, text="server error")

        with mock.patch.object(parsers, "GOODREADS_RSS_PER_PAGE", 3), \
             mock.patch.object(parsers, "GOODREADS_RSS_MAX_PAGES", 5), \
             mock.patch.object(parsers, "GOODREADS_RSS_PAGE_DELAY_SECONDS", 0):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                warnings: list[str] = []
                books, failed = await parsers._fetch_shelf_paginated(client, "1", "read", {}, warnings)

        self.assertFalse(failed)
        self.assertEqual(len(books), 3)
        self.assertTrue(any("malformed response" in w for w in warnings))

    async def test_blank_title_is_skipped_with_visible_warning(self):
        page = _rss_xml([
            {"title": "", "isbn": "9780000000001"},
            {"title": "Usable Book", "isbn": "9780000000002"},
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=page)

        with mock.patch.object(parsers, "GOODREADS_RSS_PER_PAGE", 200), \
             mock.patch.object(parsers, "GOODREADS_RSS_MAX_PAGES", 2):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                warnings: list[str] = []
                books, failed = await parsers._fetch_shelf_paginated(client, "1", "read", {}, warnings)

        self.assertFalse(failed)
        self.assertEqual([book["title"] for book in books], ["Usable Book"])
        self.assertTrue(any("blank title" in warning for warning in warnings))


class DedupeBooksTests(unittest.TestCase):
    def test_dedupes_by_isbn_first_occurrence_wins(self):
        books = [
            {"title": "A", "author": "X", "isbn": "111", "my_rating": 5},
            {"title": "A (dup)", "author": "X", "isbn": "111", "my_rating": 1},
            {"title": "B", "author": "Y", "isbn": "", "my_rating": 3},
        ]
        out = parsers.dedupe_books(books)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["title"], "A")

    def test_dedupes_by_title_author_when_no_isbn(self):
        books = [
            {"title": "Same Title", "author": "Same Author", "isbn": "", "my_rating": 5},
            {"title": "same title", "author": "same author", "isbn": "", "my_rating": 2},
        ]
        out = parsers.dedupe_books(books)
        self.assertEqual(len(out), 1)

    def test_book_id_takes_precedence_over_isbn_and_title(self):
        # Two entries share the same Goodreads book_id (e.g. Goodreads
        # returned slightly different ISBN/title text for the same book
        # across pages) — book_id must still collapse them to one.
        books = [
            {"title": "Original Title", "author": "X", "isbn": "9780000000001", "my_rating": 5, "_book_id": "12345"},
            {"title": "Retitled Edition", "author": "X", "isbn": "9780000000009", "my_rating": 5, "_book_id": "12345"},
        ]
        out = parsers.dedupe_books(books)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Original Title")

    def test_guid_used_when_book_id_absent(self):
        books = [
            {"title": "A", "author": "X", "isbn": "", "my_rating": 5, "_guid": "goodreads.com/review/show/999"},
            {"title": "A copy", "author": "X", "isbn": "", "my_rating": 5, "_guid": "goodreads.com/review/show/999"},
        ]
        out = parsers.dedupe_books(books)
        self.assertEqual(len(out), 1)

    def test_different_book_ids_are_not_merged_despite_same_title(self):
        books = [
            {"title": "Common Title", "author": "X", "isbn": "", "my_rating": 5, "_book_id": "1"},
            {"title": "Common Title", "author": "X", "isbn": "", "my_rating": 4, "_book_id": "2"},
        ]
        out = parsers.dedupe_books(books)
        self.assertEqual(len(out), 2)


class ParseFeedFieldExtractionTests(unittest.TestCase):
    """Live schema confirmation: book_id, average_rating, user_read_at,
    user_shelves, and guid are un-namespaced plain tags; user_read_at (not
    user_date_read) is the real read-date field."""

    def test_year_read_extracted_from_user_read_at_field(self):
        xml = _rss_xml([
            {"title": "T", "isbn": "1", "rating": 5, "user_read_at": "Wed, 01 Jan 2020 00:00:00 -0800"},
        ])
        books = parsers.parse_feed(xml, "read")
        self.assertEqual(books[0]["year_read"], 2020)

    def test_book_id_and_guid_captured_as_internal_fields(self):
        xml = _rss_xml([
            {"title": "T", "isbn": "1", "rating": 5, "book_id": "555", "guid": "goodreads.com/review/show/999"},
        ])
        books = parsers.parse_feed(xml, "read")
        self.assertEqual(books[0]["_book_id"], "555")
        self.assertEqual(books[0]["_guid"], "goodreads.com/review/show/999")

    def test_book_id_falls_back_to_guid_url_when_tag_missing(self):
        item_xml = """<item>
            <title>T</title>
            <guid>https://www.goodreads.com/book/show/424242-some-book</guid>
            <isbn>1</isbn>
            <user_rating>5</user_rating>
        </item>"""
        xml = f"<?xml version='1.0'?><rss><channel>{item_xml}</channel></rss>"
        books = parsers.parse_feed(xml, "read")
        self.assertEqual(books[0]["_book_id"], "424242")

    def test_book_id_and_guid_are_stripped_from_final_output(self):
        xml = _rss_xml([
            {"title": "T", "isbn": "1", "rating": 5, "book_id": "555", "guid": "goodreads.com/review/show/999"},
        ])
        raw = parsers.parse_feed(xml, "read")
        stripped = parsers._strip_internal(raw[0])
        self.assertNotIn("_book_id", stripped)
        self.assertNotIn("_guid", stripped)


class DnfMergeTests(unittest.IsolatedAsyncioTestCase):
    async def test_dnf_combines_dedicated_shelf_with_tags_found_in_read_feed(self):
        # A dedicated DNF shelf book, plus a "read"-shelf book tagged with a
        # DNF-style user shelf — both must end up in the merged DNF list.
        read_page = _rss_xml([
            {"title": "Actually Finished", "isbn": "1", "rating": 4, "shelf": "read"},
            {"title": "Abandoned But Tagged Read", "isbn": "2", "rating": 0, "shelf": "read", "user_shelves": "did-not-finish"},
        ])
        dnf_page = _rss_xml([
            {"title": "Dedicated DNF Book", "isbn": "3", "rating": 0, "shelf": "did-not-finish"},
        ])
        empty_page = _rss_xml([])

        def handler(request: httpx.Request) -> httpx.Response:
            shelf = request.url.params.get("shelf")
            page = int(request.url.params.get("page", "1"))
            if page > 1:
                return httpx.Response(200, text=empty_page)
            if shelf == "read":
                return httpx.Response(200, text=read_page)
            if shelf == "did-not-finish":
                return httpx.Response(200, text=dnf_page)
            return httpx.Response(200, text=empty_page)

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient  # capture BEFORE patching to avoid self-recursion

        def _factory(*args, **kwargs):
            kwargs.pop("transport", None)
            return real_async_client(transport=transport)

        with mock.patch.object(parsers, "GOODREADS_RSS_PER_PAGE", 200), \
             mock.patch.object(parsers, "GOODREADS_RSS_MAX_PAGES", 5), \
             mock.patch.object(parsers, "GOODREADS_RSS_PAGE_DELAY_SECONDS", 0), \
             mock.patch("httpx.AsyncClient", side_effect=_factory):
            result = await parsers.parse_rss("123456")

        dnf_titles = {b["title"] for b in result["dnf"]}
        self.assertEqual(dnf_titles, {"Abandoned But Tagged Read", "Dedicated DNF Book"})
        self.assertEqual(result["shelf_counts"]["dnf"], 2)
        # The DNF-tagged read-shelf book must not also appear as a "read" book.
        book_titles = {b["title"] for b in result["books"]}
        self.assertIn("Actually Finished", book_titles)
        self.assertNotIn("Abandoned But Tagged Read", book_titles)


if __name__ == "__main__":
    unittest.main()
