"""Tests for sampling.build_representative_sample (deterministic stratified
sampling, shared by dna.py and llm_battle.py) and dna.py's consensus-gated
contrarian-score prompt section.

dna.py lazily imports the cerebras SDK only inside functions that need it, so
importing the module for these pure-function tests never requires the SDK.
"""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import dna
from sampling import build_representative_sample


def _make_books(n, rating, start_year=2000, author_prefix="Author"):
    return [
        {
            "title": f"Book {rating}-{i}",
            "author": f"{author_prefix} {i}",
            "my_rating": rating,
            "year_read": start_year + i,
            "avg_rating": 0.0,
        }
        for i in range(n)
    ]


class BuildRepresentativeSampleTests(unittest.TestCase):
    def test_returns_all_books_when_under_target(self):
        books = _make_books(5, 4)
        sample = build_representative_sample(books, target=80)
        self.assertEqual(len(sample), 5)

    def test_is_deterministic_across_repeated_calls(self):
        books = _make_books(20, 5) + _make_books(20, 3) + _make_books(20, 1)
        sample1 = build_representative_sample(books, target=10)
        sample2 = build_representative_sample(books, target=10)
        self.assertEqual(sample1, sample2)

    def test_samples_across_multiple_rating_buckets_not_just_top_rated(self):
        # 100 five-star books plus a handful of 1-2 star books — the old
        # "highest-rated N" approach would return only 5-star books.
        books = _make_books(100, 5) + _make_books(5, 1) + _make_books(5, 2)
        sample = build_representative_sample(books, target=20)
        ratings_present = {b["my_rating"] for b in sample}
        self.assertIn(1, ratings_present)
        self.assertIn(2, ratings_present)
        self.assertIn(5, ratings_present)

    def test_sample_size_matches_target_when_enough_books_exist(self):
        books = _make_books(50, 4) + _make_books(50, 2)
        sample = build_representative_sample(books, target=30)
        self.assertEqual(len(sample), 30)

    def test_spreads_across_recency_within_a_bucket(self):
        # 40 five-star books spanning 40 distinct years — a naive "first N"
        # or "most recent N" sample would only ever show one end of the range.
        books = _make_books(40, 5, start_year=1980)
        sample = build_representative_sample(books, target=10)
        years = sorted(b["year_read"] for b in sample if b.get("my_rating") == 5)
        self.assertGreater(years[-1] - years[0], 15)  # spans a wide range, not clustered

    def test_output_is_canonically_sorted_rating_desc_then_recency_then_title(self):
        books = _make_books(3, 5, start_year=2020) + _make_books(3, 3, start_year=2020)
        sample = build_representative_sample(books, target=80)
        ratings = [b["my_rating"] for b in sample]
        self.assertEqual(ratings, sorted(ratings, reverse=True))

    def test_ties_broken_deterministically_by_title_then_author(self):
        books = [
            {"title": "Zeta", "author": "A", "my_rating": 5, "year_read": 2020},
            {"title": "Alpha", "author": "B", "my_rating": 5, "year_read": 2020},
        ]
        sample = build_representative_sample(books, target=80)
        self.assertEqual([b["title"] for b in sample], ["Alpha", "Zeta"])

    def test_ignores_out_of_range_ratings_when_bucketing_kicks_in(self):
        # Only exercises exclusion once total > target, so the bucketed path
        # (which only collects ratings 1-5) actually runs instead of the
        # short-circuit "return everything" path for small collections.
        books = _make_books(30, 4) + _make_books(30, 2) + [
            {"title": "Bad Rating", "author": "X", "my_rating": 0, "year_read": 2020}
        ]
        sample = build_representative_sample(books, target=10)
        titles = [b["title"] for b in sample]
        self.assertNotIn("Bad Rating", titles)

    def test_unrated_books_are_excluded_even_when_under_target(self):
        books = _make_books(3, 4) + [
            {"title": f"Unrated {index}", "author": "X", "my_rating": 0}
            for index in range(100)
        ]
        sample = build_representative_sample(books, target=80)
        self.assertEqual(len(sample), 3)
        self.assertTrue(all(book["my_rating"] == 4 for book in sample))

    def test_target_smaller_than_populated_bucket_count_is_never_exceeded(self):
        books = [
            _make_books(1, rating)[0]
            for rating in range(1, 6)
        ]
        sample = build_representative_sample(books, target=2)
        self.assertEqual(len(sample), 2)

    def test_zero_target_returns_empty_sample(self):
        self.assertEqual(build_representative_sample(_make_books(5, 4), target=0), [])


class ConsensusGatingTests(unittest.TestCase):
    def test_insufficient_data_marks_no_consensus(self):
        books = _make_books(20, 4)  # avg_rating all 0.0 -> no consensus data
        has_consensus, with_avg = dna._has_consensus_data(books)
        self.assertFalse(has_consensus)
        self.assertEqual(with_avg, [])

    def test_sufficient_data_marks_consensus_available(self):
        books = _make_books(20, 4)
        for b in books:
            b["avg_rating"] = 4.1
        has_consensus, with_avg = dna._has_consensus_data(books)
        self.assertTrue(has_consensus)
        self.assertEqual(len(with_avg), 20)

    def test_prompt_explicitly_says_unavailable_when_no_consensus(self):
        books = _make_books(5, 4)  # below CONSENSUS_MIN_BOOKS and all avg_rating=0
        section, has_consensus = dna._build_contrarian_section(books)
        self.assertFalse(has_consensus)
        self.assertIn("null", section)
        self.assertIn("Not enough", section)

    def test_prompt_includes_evidence_when_consensus_available(self):
        books = _make_books(20, 4)
        for b in books:
            b["avg_rating"] = 3.0  # reader consistently rates higher than consensus
        section, has_consensus = dna._build_contrarian_section(books)
        self.assertTrue(has_consensus)
        self.assertIn("delta", section)


class DnaRawOutputValidationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _profile():
        return {
            "reader_archetype": "Test Reader",
            "one_liner": "A complete test profile.",
            "taste_dimensions": {
                "prose_density": 5,
                "pacing_preference": 5,
                "fiction_ratio": 50,
                "intellectual_depth": 5,
                "emotional_intensity": 5,
                "contrarian_score": 5,
            },
            "top_themes": [],
            "avoid_themes": [],
            "favorite_authors": [],
            "taste_summary": "A complete taste summary.",
            "blind_spot_genres": [],
            "top_books": [],
        }

    async def _build_with_profile(self, profile):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(profile))
                )
            ]
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: object())
            )
        )
        books = _make_books(20, 4)
        for book in books:
            book["avg_rating"] = 4.0
        with mock.patch.object(dna, "_get_client", return_value=fake_client), \
             mock.patch.object(dna, "call_with_limit", new=mock.AsyncMock(return_value=response)):
            return await dna.build_dna_profile(books)

    async def test_non_list_top_books_is_rejected_before_enrichment(self):
        profile = self._profile()
        profile["top_books"] = 1
        with self.assertRaises(ValueError):
            await self._build_with_profile(profile)

    async def test_non_finite_contrarian_score_is_rejected_not_clamped(self):
        profile = self._profile()
        profile["taste_dimensions"]["contrarian_score"] = float("nan")
        with self.assertRaises(ValueError):
            await self._build_with_profile(profile)


if __name__ == "__main__":
    unittest.main()
