"""Tests for llm_battle's Open Library ISBN verification/enrichment:
llm_battle.enrich_recommendations_with_isbn and its helpers.

LLM-supplied ISBNs should never be trusted blindly. These tests cover:
  - A supplied ISBN that resolves to a matching title/author is kept.
  - A supplied ISBN that resolves to a different book is dropped (not
    silently kept, not raised as an error).
  - A missing/implausible ISBN is filled in via a title+author search when
    Open Library has a match.
  - A lookup outage (network error / malformed response) never fails the
    caller: title/author/reason survive untouched, the ISBN is omitted, and
    a warning is returned.
  - asyncio.CancelledError from the lookup is never swallowed.
  - Lookups are deduplicated across recs that share the same canonical
    title + supplied ISBN (e.g. a "consensus pick" recommended by both
    models) -- only one network call should be made for such a group.
  - Concurrency is bounded by ISBN_VERIFY_CONCURRENCY.

All Open Library HTTP access is mocked via llm_battle.lookup_open_library --
no real network access is required or performed.
"""
import asyncio
import unittest
from unittest import mock

import llm_battle


def _rec(title, author="", isbn=""):
    return {"title": title, "author": author, "isbn": isbn, "year": "", "why": "reasons", "comfort_zone": True, "hidden_gem": False}


class EnrichRecommendationsWithIsbnTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_list_returns_no_warnings_and_no_lookup(self):
        with mock.patch.object(llm_battle, "lookup_open_library") as mocked:
            warnings = await llm_battle.enrich_recommendations_with_isbn([])
        self.assertEqual(warnings, [])
        mocked.assert_not_called()

    async def test_verified_matching_isbn_is_kept(self):
        rec = _rec("Great Book", "Jane Author", "9780000000002")

        async def fake_lookup(*, isbn=None, title=None, author=None, timeout=None):
            self.assertEqual(isbn, "9780000000002")
            return {"title": "Great Book", "author": "Jane Author"}, None

        with mock.patch.object(llm_battle, "lookup_open_library", side_effect=fake_lookup):
            warnings = await llm_battle.enrich_recommendations_with_isbn([rec])

        self.assertEqual(rec["isbn"], "9780000000002")
        self.assertEqual(warnings, [])

    async def test_isbn_resolving_to_a_different_book_is_dropped_without_warning(self):
        rec = _rec("Great Book", "Jane Author", "9780000000002")

        async def fake_lookup(*, isbn=None, title=None, author=None, timeout=None):
            return {"title": "Completely Different Book", "author": "Someone Else"}, None

        with mock.patch.object(llm_battle, "lookup_open_library", side_effect=fake_lookup):
            warnings = await llm_battle.enrich_recommendations_with_isbn([rec])

        self.assertEqual(rec["isbn"], "")
        self.assertEqual(warnings, [])  # a confirmed mismatch isn't an outage — no warning needed

    async def test_missing_isbn_is_resolved_via_title_author_search(self):
        rec = _rec("Great Book", "Jane Author", isbn="")

        async def fake_lookup(*, isbn=None, title=None, author=None, timeout=None):
            self.assertIsNone(isbn)
            self.assertEqual(title, "Great Book")
            return {"title": "Great Book", "author": "Jane Author", "isbn": "9780000000019"}, None

        with mock.patch.object(llm_battle, "lookup_open_library", side_effect=fake_lookup):
            warnings = await llm_battle.enrich_recommendations_with_isbn([rec])

        self.assertEqual(rec["isbn"], "9780000000019")
        self.assertEqual(warnings, [])

    async def test_unrelated_title_search_result_is_not_accepted(self):
        rec = _rec("Great Book", "Jane Author", isbn="")

        async def fake_lookup(*, isbn=None, title=None, author=None, timeout=None):
            return {
                "title": "A Different Book",
                "author": "Someone Else",
                "isbn": "9780000000019",
            }, None

        with mock.patch.object(llm_battle, "lookup_open_library", side_effect=fake_lookup):
            warnings = await llm_battle.enrich_recommendations_with_isbn([rec])

        self.assertEqual(rec["isbn"], "")
        self.assertEqual(warnings, [])

    async def test_missing_author_evidence_is_not_accepted_when_author_is_known(self):
        rec = _rec("Great Book", "Jane Author", isbn="")

        async def fake_lookup(*, isbn=None, title=None, author=None, timeout=None):
            return {"title": "Great Book", "author": "", "isbn": "9780000000019"}, None

        with mock.patch.object(llm_battle, "lookup_open_library", side_effect=fake_lookup):
            await llm_battle.enrich_recommendations_with_isbn([rec])

        self.assertEqual(rec["isbn"], "")

    async def test_implausible_isbn_shape_skips_verification_and_falls_back_to_search(self):
        # "N/A" / a hallucinated non-ISBN string should never be sent to the
        # isbn= lookup path; it should fall through to title+author search.
        rec = _rec("Great Book", "Jane Author", isbn="N/A")
        calls = []

        async def fake_lookup(*, isbn=None, title=None, author=None, timeout=None):
            calls.append({"isbn": isbn, "title": title})
            return {"title": "Great Book", "author": "Jane Author", "isbn": "9780000000019"}, None

        with mock.patch.object(llm_battle, "lookup_open_library", side_effect=fake_lookup):
            await llm_battle.enrich_recommendations_with_isbn([rec])

        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0]["isbn"])
        self.assertEqual(calls[0]["title"], "Great Book")
        self.assertEqual(rec["isbn"], "9780000000019")

    async def test_lookup_outage_omits_isbn_and_warns_without_failing(self):
        rec = _rec("Great Book", "Jane Author", "9780000000002")

        async def fake_lookup(*, isbn=None, title=None, author=None, timeout=None):
            return None, "Open Library lookup failed (connection timed out)"

        with mock.patch.object(llm_battle, "lookup_open_library", side_effect=fake_lookup):
            warnings = await llm_battle.enrich_recommendations_with_isbn([rec])

        self.assertEqual(rec["isbn"], "")
        self.assertEqual(rec["title"], "Great Book")  # untouched
        self.assertEqual(rec["why"], "reasons")  # untouched
        self.assertEqual(len(warnings), 1)
        self.assertIn("Great Book", warnings[0])

    async def test_cancelled_error_is_never_swallowed(self):
        rec = _rec("Great Book", "Jane Author", "9780000000002")

        async def fake_lookup(*, isbn=None, title=None, author=None, timeout=None):
            raise asyncio.CancelledError()

        with mock.patch.object(llm_battle, "lookup_open_library", side_effect=fake_lookup):
            with self.assertRaises(asyncio.CancelledError):
                await llm_battle.enrich_recommendations_with_isbn([rec])

    async def test_consensus_pick_across_both_models_deduplicates_to_one_lookup(self):
        # Both models recommend the same book with the same supplied ISBN —
        # this should trigger exactly one Open Library lookup, not two.
        rec_a = _rec("Shared Pick", "Same Author", "9780000000002")
        rec_b = _rec("Shared Pick", "Same Author", "9780000000002")
        call_count = {"n": 0}

        async def fake_lookup(*, isbn=None, title=None, author=None, timeout=None):
            call_count["n"] += 1
            return {"title": "Shared Pick", "author": "Same Author"}, None

        with mock.patch.object(llm_battle, "lookup_open_library", side_effect=fake_lookup):
            await llm_battle.enrich_recommendations_with_isbn([rec_a, rec_b])

        self.assertEqual(call_count["n"], 1)
        self.assertEqual(rec_a["isbn"], "9780000000002")
        self.assertEqual(rec_b["isbn"], "9780000000002")

    async def test_different_supplied_isbns_for_same_title_are_looked_up_separately(self):
        rec_a = _rec("Shared Pick", "Same Author", "9780000000002")
        rec_b = _rec("Shared Pick", "Same Author", "9780000000019")
        seen_isbns = []

        async def fake_lookup(*, isbn=None, title=None, author=None, timeout=None):
            seen_isbns.append(isbn)
            return {"title": "Shared Pick", "author": "Same Author"}, None

        with mock.patch.object(llm_battle, "lookup_open_library", side_effect=fake_lookup):
            await llm_battle.enrich_recommendations_with_isbn([rec_a, rec_b])

        self.assertEqual(sorted(seen_isbns), ["9780000000002", "9780000000019"])

    async def test_concurrency_is_bounded_by_isbn_verify_concurrency(self):
        concurrency_limit = 2
        in_flight = {"current": 0, "max": 0}
        lock = asyncio.Lock()
        recs = [_rec(f"Book {i}", "Author", isbn="") for i in range(6)]

        async def fake_lookup(*, isbn=None, title=None, author=None, timeout=None):
            async with lock:
                in_flight["current"] += 1
                in_flight["max"] = max(in_flight["max"], in_flight["current"])
            try:
                await asyncio.sleep(0.02)
                return {"title": title, "author": author, "isbn": "9780000000002"}, None
            finally:
                async with lock:
                    in_flight["current"] -= 1

        with mock.patch.object(llm_battle, "ISBN_VERIFY_CONCURRENCY", concurrency_limit), \
             mock.patch.object(llm_battle, "lookup_open_library", side_effect=fake_lookup):
            await llm_battle.enrich_recommendations_with_isbn(recs)

        self.assertLessEqual(in_flight["max"], concurrency_limit)
        self.assertGreater(in_flight["max"], 1)  # actually ran concurrently, not serially

    async def test_unexpected_exception_in_one_group_is_isolated_and_warned(self):
        rec_ok = _rec("Good Book", "Author", isbn="")
        rec_bad = _rec("Bad Book", "Author", isbn="")

        async def fake_lookup(*, isbn=None, title=None, author=None, timeout=None):
            if title == "Bad Book":
                raise RuntimeError("boom")
            return {"title": title, "author": author, "isbn": "9780000000002"}, None

        with mock.patch.object(llm_battle, "lookup_open_library", side_effect=fake_lookup):
            warnings = await llm_battle.enrich_recommendations_with_isbn([rec_ok, rec_bad])

        self.assertEqual(rec_ok["isbn"], "9780000000002")
        self.assertEqual(rec_bad["isbn"], "")
        self.assertTrue(any("unexpectedly" in w for w in warnings))


class IsbnHelperFunctionTests(unittest.TestCase):
    def test_normalize_isbn_strips_hyphens_and_whitespace(self):
        self.assertEqual(llm_battle._normalize_isbn(" 978-0-000-00000-2 "), "9780000000002")

    def test_is_plausible_isbn_accepts_isbn10_and_isbn13(self):
        self.assertTrue(llm_battle._is_plausible_isbn("9780000000002"))  # 13 digits
        self.assertTrue(llm_battle._is_plausible_isbn("080442957X"))  # 10 chars, X check digit

    def test_is_plausible_isbn_rejects_garbage(self):
        self.assertFalse(llm_battle._is_plausible_isbn(""))
        self.assertFalse(llm_battle._is_plausible_isbn("N/A"))
        self.assertFalse(llm_battle._is_plausible_isbn("12345"))

    def test_authors_plausibly_match_is_lenient_about_formatting(self):
        self.assertTrue(llm_battle._authors_plausibly_match("J.R.R. Tolkien", "Tolkien, J. R. R."))
        self.assertTrue(llm_battle._authors_plausibly_match("", "Jane Author"))  # unknown side never contradicts

    def test_authors_plausibly_match_rejects_clearly_different_authors(self):
        self.assertFalse(llm_battle._authors_plausibly_match("Jane Author", "John Smith"))


if __name__ == "__main__":
    unittest.main()
