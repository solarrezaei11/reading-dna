"""Tests for predict.resolve_book (Open Library outage/invalid-response vs
legitimate no-match distinction) and predict.predict_rating's warnings
surfacing + asyncio.CancelledError-safe gather handling.

predict.py imports numpy/httpx eagerly but only touches the Cerebras SDK
inside functions called via llm_battle.call_model, which we mock here, so
importing this module for tests never requires network access or the
cerebras SDK's real client.
"""
import asyncio
import unittest
from unittest import mock

import httpx

import open_library
import predict


class ResolveBookOutageVsNoMatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_legitimate_no_match_returns_no_warning(self):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"docs": []}

        async def fake_get(self, url, params=None):
            return FakeResponse()

        with mock.patch.object(httpx.AsyncClient, "get", fake_get):
            candidate, warning = await predict.resolve_book("A Book Nobody Wrote", "Nobody")

        self.assertIsNone(candidate)
        self.assertIsNone(warning)

    async def test_network_outage_returns_a_warning_not_a_silent_none(self):
        async def fake_get(self, url, params=None):
            raise httpx.ConnectTimeout("connection timed out")

        with mock.patch.object(httpx.AsyncClient, "get", fake_get):
            candidate, warning = await predict.resolve_book("Some Title")

        self.assertIsNone(candidate)
        self.assertIsNotNone(warning)
        self.assertIn("Open Library", warning)

    async def test_malformed_response_shape_returns_a_warning(self):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return ["not", "a", "dict"]  # unexpected shape

        async def fake_get(self, url, params=None):
            return FakeResponse()

        with mock.patch.object(httpx.AsyncClient, "get", fake_get):
            candidate, warning = await predict.resolve_book("Some Title")

        self.assertIsNone(candidate)
        self.assertIsNotNone(warning)

    async def test_non_list_docs_returns_a_warning(self):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"docs": {"title": "not a list"}}

        async def fake_get(self, url, params=None):
            return FakeResponse()

        with mock.patch.object(httpx.AsyncClient, "get", fake_get):
            candidate, warning = await predict.resolve_book("Some Title")

        self.assertIsNone(candidate)
        self.assertIn("docs", warning)

    async def test_malformed_document_field_returns_a_warning(self):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"docs": [{"title": "Some Title", "author_name": "not-a-list"}]}

        async def fake_get(self, url, params=None):
            return FakeResponse()

        with mock.patch.object(httpx.AsyncClient, "get", fake_get):
            candidate, warning = await predict.resolve_book("Some Title")

        self.assertIsNone(candidate)
        self.assertIn("author_name", warning)

    async def test_successful_match_returns_candidate_and_no_warning(self):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "docs": [
                        {
                            "title": "Found Book",
                            "author_name": ["Found Author"],
                            "first_publish_year": 1999,
                            "subject": ["Fiction"],
                            "isbn": ["9780000000002"],
                            "cover_i": 42,
                        }
                    ]
                }

        async def fake_get(self, url, params=None):
            return FakeResponse()

        with mock.patch.object(httpx.AsyncClient, "get", fake_get):
            candidate, warning = await predict.resolve_book("Found Book", "Found Author")

        self.assertIsNone(warning)
        self.assertEqual(candidate["title"], "Found Book")
        self.assertEqual(candidate["isbn"], "9780000000002")

    async def test_lookup_preserves_missing_author_as_missing_evidence(self):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"docs": [{"title": "Found Book", "isbn": ["9780000000002"]}]}

        async def fake_get(self, url, params=None):
            return FakeResponse()

        with mock.patch.object(httpx.AsyncClient, "get", fake_get):
            candidate, warning = await open_library.lookup_open_library(
                title="Found Book",
                author="Queried Author",
            )

        self.assertIsNone(warning)
        self.assertEqual(candidate["author"], "")

    async def test_prediction_display_can_retain_queried_author(self):
        with mock.patch.object(
            predict,
            "lookup_open_library",
            new=mock.AsyncMock(
                return_value=(
                    {"title": "Found Book", "author": "", "isbn": "9780000000002"},
                    None,
                )
            ),
        ):
            candidate, warning = await predict.resolve_book("Found Book", "Queried Author")

        self.assertIsNone(warning)
        self.assertEqual(candidate["author"], "Queried Author")

    async def test_cancelled_error_is_never_swallowed(self):
        async def fake_get(self, url, params=None):
            raise asyncio.CancelledError()

        with mock.patch.object(httpx.AsyncClient, "get", fake_get):
            with self.assertRaises(asyncio.CancelledError):
                await predict.resolve_book("Some Title")


class FindAlreadyReadTests(unittest.TestCase):
    """Author disambiguation for find_already_read: when the frontend
    supplies an author, matching requires both a canonical title match AND
    a plausible author match, so an ambiguous/common title on the shelf
    isn't conflated with a different book by a different author. Title-only
    (substring-tolerant) matching is preserved as a fallback when no author
    is supplied, for backward compatibility."""

    BOOKS = [
        {"title": "Circe", "author": "Madeline Miller", "my_rating": 5, "isbn": "111"},
        {"title": "The Circle", "author": "Dave Eggers", "my_rating": 3, "isbn": "222"},
    ]

    def test_title_only_fallback_matches_when_no_author_supplied(self):
        # Preserves prior behavior: title-only substring/equality matching.
        result = predict.find_already_read("Circe", self.BOOKS)
        self.assertIsNotNone(result)
        self.assertEqual(result["isbn"], "111")

    def test_matching_title_and_author_resolves_correctly(self):
        result = predict.find_already_read("Circe", self.BOOKS, author="Madeline Miller")
        self.assertIsNotNone(result)
        self.assertEqual(result["isbn"], "111")

    def test_author_mismatch_on_a_shared_or_similar_title_is_not_a_false_match(self):
        # "Circe" vs "The Circle" are similar enough that a naive title-only
        # substring match could conflate them; supplying the correct author
        # for a *different* book than what's on the shelf must not match.
        result = predict.find_already_read("Circe", self.BOOKS, author="Someone Else")
        self.assertIsNone(result)

    def test_author_formatting_differences_are_tolerated(self):
        books = [{"title": "The Hobbit", "author": "Tolkien, J. R. R.", "my_rating": 4}]
        result = predict.find_already_read("The Hobbit", books, author="J.R.R. Tolkien")
        self.assertIsNotNone(result)

    def test_no_match_when_title_differs_even_with_matching_author(self):
        result = predict.find_already_read("A Completely Different Book", self.BOOKS, author="Madeline Miller")
        self.assertIsNone(result)


class PredictRatingWarningsAndCancellationTests(unittest.IsolatedAsyncioTestCase):
    DNA = {
        "reader_archetype": "Test",
        "taste_summary": "summary",
        "top_themes": [],
        "avoid_themes": [],
        "taste_dimensions": {},
    }

    async def test_resolve_warning_surfaces_in_payload_warnings(self):
        async def fake_resolve_book(title, author=None):
            return None, "Open Library lookup failed (boom); using unresolved book metadata."

        async def fake_call_model(model, prompt, retries=3, system_prompt=None):
            return {
                "predicted_rating": 4.0,
                "confidence": 0.5,
                "why": "reasons",
                "drivers": [],
                "_meta": {},
            }

        with mock.patch.object(predict, "resolve_book", side_effect=fake_resolve_book), \
             mock.patch.object(predict, "call_model", side_effect=fake_call_model):
            result = await predict.predict_rating("Some Title", None, self.DNA, books=[])

        self.assertIn("warnings", result)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("Open Library", result["warnings"][0])

    async def test_no_warning_on_legitimate_no_match(self):
        async def fake_resolve_book(title, author=None):
            return None, None

        async def fake_call_model(model, prompt, retries=3, system_prompt=None):
            return {"predicted_rating": 4.0, "confidence": 0.5, "why": "reasons", "drivers": [], "_meta": {}}

        with mock.patch.object(predict, "resolve_book", side_effect=fake_resolve_book), \
             mock.patch.object(predict, "call_model", side_effect=fake_call_model):
            result = await predict.predict_rating("Some Title", None, self.DNA, books=[])

        self.assertEqual(result["warnings"], [])

    async def test_cancelled_error_from_one_model_call_propagates_not_swallowed(self):
        async def fake_resolve_book(title, author=None):
            return None, None

        async def fake_call_model(model, prompt, retries=3, system_prompt=None):
            if model == "gpt-oss-120b":
                raise asyncio.CancelledError()
            return {"predicted_rating": 4.0, "confidence": 0.5, "why": "reasons", "drivers": [], "_meta": {}}

        with mock.patch.object(predict, "resolve_book", side_effect=fake_resolve_book), \
             mock.patch.object(predict, "call_model", side_effect=fake_call_model):
            with self.assertRaises(asyncio.CancelledError):
                await predict.predict_rating("Some Title", None, self.DNA, books=[])

    async def test_one_model_error_isolated_other_prediction_preserved(self):
        async def fake_resolve_book(title, author=None):
            return None, None

        async def fake_call_model(model, prompt, retries=3, system_prompt=None):
            if model == "gpt-oss-120b":
                raise RuntimeError("model unavailable")
            return {"predicted_rating": 4.0, "confidence": 0.5, "why": "reasons", "drivers": [], "_meta": {}}

        with mock.patch.object(predict, "resolve_book", side_effect=fake_resolve_book), \
             mock.patch.object(predict, "call_model", side_effect=fake_call_model):
            result = await predict.predict_rating("Some Title", None, self.DNA, books=[])

        predictions = result["predictions"]
        errored = [p for p in predictions.values() if "error" in p]
        succeeded = [p for p in predictions.values() if "error" not in p]
        self.assertEqual(len(errored), 1)
        self.assertEqual(len(succeeded), 1)


if __name__ == "__main__":
    unittest.main()
