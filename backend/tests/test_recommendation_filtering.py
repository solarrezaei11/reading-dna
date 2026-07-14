"""Tests for llm_battle.validate_and_filter_recommendations / canonical_title.

llm_battle.py lazily imports the cerebras SDK only inside functions that need
it, so importing the module for these pure-function tests never requires the
SDK to be installed.
"""
import unittest

import llm_battle


class CanonicalTitleTests(unittest.TestCase):
    def test_strips_leading_article_and_lowercases(self):
        self.assertEqual(llm_battle.canonical_title("The Great Gatsby"), "great gatsby")

    def test_strips_punctuation_and_collapses_whitespace(self):
        self.assertEqual(llm_battle.canonical_title("A  Tale, of   Two Cities!"), "tale of two cities")

    def test_different_articles_collide_to_same_canonical_form(self):
        self.assertEqual(llm_battle.canonical_title("An Odyssey"), llm_battle.canonical_title("The Odyssey  "))

    def test_empty_and_none_safe(self):
        self.assertEqual(llm_battle.canonical_title(""), "")
        self.assertEqual(llm_battle.canonical_title(None), "")

    def test_diacritics_and_case_are_folded(self):
        self.assertEqual(llm_battle.canonical_title("Café Society"), llm_battle.canonical_title("CAFE SOCIETY"))

    def test_distinct_non_latin_titles_do_not_collapse(self):
        self.assertNotEqual(llm_battle.canonical_title("雪国"), llm_battle.canonical_title("人間失格"))


class BuildExcludeIndexAndIsExcludedTests(unittest.TestCase):
    """Focused tests for the canonical-title(+author) exclude index used by
    validate_and_filter_recommendations, independent of Pydantic validation,
    covering the "two distinct books share a title" edge case directly."""

    def test_build_exclude_index_merges_multiple_book_lists(self):
        index = llm_battle.build_exclude_index(
            [{"title": "Book A", "author": "Author A"}],
            [{"title": "Book B", "author": "Author B"}],
        )
        self.assertIn(llm_battle.canonical_title("Book A"), index)
        self.assertIn(llm_battle.canonical_title("Book B"), index)

    def test_build_exclude_index_skips_entries_with_blank_title(self):
        index = llm_battle.build_exclude_index([{"title": "", "author": "Someone"}])
        self.assertEqual(index, {})

    def test_two_distinct_books_sharing_a_title_are_kept_separate_by_author(self):
        # The shelf has "Circe" by Madeline Miller. A recommendation for a
        # different, unrelated book that happens to share the title "Circe"
        # but has a distinct, known author must NOT be treated as the same
        # book — canonical title alone is not enough evidence here.
        index = llm_battle.build_exclude_index([{"title": "Circe", "author": "Madeline Miller"}])
        self.assertFalse(llm_battle._is_excluded("Circe", "A Totally Different Author", index))
        self.assertTrue(llm_battle._is_excluded("Circe", "Madeline Miller", index))

    def test_author_formatting_differences_still_match(self):
        index = llm_battle.build_exclude_index([{"title": "The Hobbit", "author": "Tolkien, J. R. R."}])
        self.assertTrue(llm_battle._is_excluded("The Hobbit", "J.R.R. Tolkien", index))

    def test_falls_back_to_title_only_when_shelf_author_missing(self):
        index = llm_battle.build_exclude_index([{"title": "Circe", "author": ""}])
        self.assertTrue(llm_battle._is_excluded("Circe", "Madeline Miller", index))

    def test_falls_back_to_title_only_when_candidate_author_missing(self):
        index = llm_battle.build_exclude_index([{"title": "Circe", "author": "Madeline Miller"}])
        self.assertTrue(llm_battle._is_excluded("Circe", "", index))

    def test_no_match_when_title_is_not_on_the_shelf_at_all(self):
        index = llm_battle.build_exclude_index([{"title": "Circe", "author": "Madeline Miller"}])
        self.assertFalse(llm_battle._is_excluded("A Completely Different Title", "Madeline Miller", index))


class ValidateAndFilterRecommendationsTests(unittest.TestCase):
    def test_valid_recommendations_pass_through(self):
        # Exactly TARGET_RECS valid picks -> no "too few" warning.
        raw = [
            {"title": f"Book {i}", "author": f"Author {i}", "why": "matches taste"}
            for i in range(llm_battle.TARGET_RECS)
        ]
        result, warnings = llm_battle.validate_and_filter_recommendations(raw, exclude_index={})
        self.assertEqual(len(result), llm_battle.TARGET_RECS)
        self.assertEqual(warnings, [])
        self.assertFalse(result[0]["on_tbr"])

    def test_below_target_recs_warns_even_above_old_minimum(self):
        # 3 valid recs used to be "acceptable" under the old MIN_ACCEPTABLE_RECS
        # threshold; TARGET_RECS-based warnings must fire below TARGET_RECS (5).
        raw = [
            {"title": "Book One", "author": "Author A", "why": "matches taste"},
            {"title": "Book Two", "author": "Author B", "why": "matches taste"},
            {"title": "Book Three", "author": "Author C", "why": "matches taste"},
        ]
        result, warnings = llm_battle.validate_and_filter_recommendations(raw, exclude_index={})
        self.assertEqual(len(result), 3)
        self.assertTrue(any("remained after de-duplication" in w for w in warnings))

    def test_caps_at_target_recs_even_if_model_overproduces(self):
        raw = [
            {"title": f"Book {i}", "author": f"Author {i}", "why": "matches taste"}
            for i in range(llm_battle.TARGET_RECS + 4)
        ]
        result, warnings = llm_battle.validate_and_filter_recommendations(raw, exclude_index={})
        self.assertEqual(len(result), llm_battle.TARGET_RECS)
        self.assertEqual(warnings, [])

    def test_drops_non_object_entries(self):
        raw = ["not a dict", {"title": "Valid Book"}]
        result, warnings = llm_battle.validate_and_filter_recommendations(raw, exclude_index={})
        self.assertEqual(len(result), 1)
        self.assertTrue(any("non-object" in w for w in warnings))

    def test_drops_invalid_missing_title(self):
        raw = [{"author": "No Title Here"}, {"title": "Has Title"}]
        result, warnings = llm_battle.validate_and_filter_recommendations(raw, exclude_index={})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Has Title")
        self.assertTrue(any("invalid recommendation" in w for w in warnings))

    def test_dedupes_same_book_but_keeps_same_title_by_different_author(self):
        raw = [
            {"title": "The Hobbit", "author": "Tolkien"},
            {"title": "the hobbit", "author": "Tolkien"},
            {"title": "A Hobbit", "author": "Someone Else"},
        ]
        result, warnings = llm_battle.validate_and_filter_recommendations(raw, exclude_index={})
        self.assertEqual(len(result), 2)
        self.assertEqual({recommendation["author"] for recommendation in result}, {"Tolkien", "Someone Else"})

    def test_tbr_match_is_retained_and_labeled(self):
        raw = [{"title": "The Left Hand of Darkness", "author": "Ursula K. Le Guin"}]
        tbr_index = llm_battle.build_exclude_index(
            [{"title": "The Left Hand of Darkness", "author": "Ursula K. Le Guin"}]
        )
        result, warnings = llm_battle.validate_and_filter_recommendations(
            raw,
            exclude_index={},
            tbr_index=tbr_index,
        )
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["on_tbr"])

    def test_filters_out_already_read_current_dnf_or_tbr_titles(self):
        raw = [
            {"title": "Already Read Book", "author": "Jordan Smith"},
            {"title": "New Book", "author": "Taylor Nguyen"},
        ]
        exclude_index = llm_battle.build_exclude_index([{"title": "Already Read Book", "author": "Jordan Smith"}])
        result, warnings = llm_battle.validate_and_filter_recommendations(raw, exclude_index=exclude_index)
        titles = [r["title"] for r in result]
        self.assertEqual(titles, ["New Book"])

    def test_warns_but_does_not_invent_when_below_minimum(self):
        raw = [{"title": "Only One Book", "author": "X"}]
        result, warnings = llm_battle.validate_and_filter_recommendations(raw, exclude_index={})
        self.assertEqual(len(result), 1)  # never padded up to MIN_ACCEPTABLE_RECS
        self.assertTrue(any("remained after de-duplication" in w for w in warnings))

    def test_zero_valid_recs_returns_empty_list_with_warning_not_error(self):
        raw = [{"author": "no title"}]
        result, warnings = llm_battle.validate_and_filter_recommendations(raw, exclude_index={})
        self.assertEqual(result, [])
        self.assertTrue(len(warnings) >= 1)

    def test_out_of_range_field_is_rejected_by_pydantic_validation(self):
        # comfort_zone/hidden_gem must be bool-coercible; a wildly wrong type
        # for title (e.g. a list) should be dropped, not crash the whole batch.
        raw = [{"title": ["not", "a", "string"]}, {"title": "Fine Book"}]
        result, warnings = llm_battle.validate_and_filter_recommendations(raw, exclude_index={})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Fine Book")

    def test_same_title_different_known_authors_is_not_falsely_excluded(self):
        # Two genuinely distinct books share a title: the reader already
        # read "Circe" by Madeline Miller, but the model recommends a
        # different, unrelated book that happens to also be titled "Circe"
        # by a different (known) author. Since both authors are known and
        # don't match, this must NOT be excluded — title-only matching would
        # have wrongly conflated the two.
        exclude_index = llm_battle.build_exclude_index(
            [{"title": "Circe", "author": "Madeline Miller"}]
        )
        raw = [{"title": "Circe", "author": "A Different Author", "why": "matches taste"}]
        result, warnings = llm_battle.validate_and_filter_recommendations(raw, exclude_index=exclude_index)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["author"], "A Different Author")

    def test_same_title_same_known_author_is_excluded(self):
        # Same scenario, but the recommended "Circe" is by the same author
        # the reader already read -> must be excluded as already-read.
        exclude_index = llm_battle.build_exclude_index(
            [{"title": "Circe", "author": "Madeline Miller"}]
        )
        raw = [
            {"title": "Circe", "author": "Madeline Miller", "why": "matches taste"},
            {"title": "New Book", "author": "New Author", "why": "matches taste"},
        ]
        result, warnings = llm_battle.validate_and_filter_recommendations(raw, exclude_index=exclude_index)
        titles = [r["title"] for r in result]
        self.assertEqual(titles, ["New Book"])

    def test_title_only_fallback_excludes_when_shelf_author_is_unknown(self):
        # The shelf entry for this title has a blank/unknown author (e.g.
        # incomplete CSV data) -> conservative title-only exclusion applies
        # even though the recommendation supplies a specific author.
        exclude_index = llm_battle.build_exclude_index(
            [{"title": "Circe", "author": ""}]
        )
        raw = [{"title": "Circe", "author": "Madeline Miller", "why": "matches taste"}]
        result, warnings = llm_battle.validate_and_filter_recommendations(raw, exclude_index=exclude_index)
        self.assertEqual(result, [])

    def test_title_only_fallback_excludes_when_recommendation_author_is_missing(self):
        # The shelf side has a known author, but the model's recommendation
        # for this title didn't supply an author -> conservative title-only
        # exclusion still applies (not enough evidence to say it's a
        # different book).
        exclude_index = llm_battle.build_exclude_index(
            [{"title": "Circe", "author": "Madeline Miller"}]
        )
        raw = [{"title": "Circe", "author": "", "why": "matches taste"}]
        result, warnings = llm_battle.validate_and_filter_recommendations(raw, exclude_index=exclude_index)
        self.assertEqual(result, [])



class RubricUnificationTests(unittest.TestCase):
    def test_rubric_keys_match_judge_prompt_dimensions(self):
        prompt = llm_battle.build_judge_prompt(
            dna={"reader_archetype": "Test", "taste_summary": "", "top_themes": [], "avoid_themes": [], "taste_dimensions": {}},
            recs=[{"title": "A Book", "author": "An Author", "why": "reasons"}],
            recommender_label="Recommender A",
        )
        for key in llm_battle.RUBRIC:
            self.assertIn(f'"{key}"', prompt)

    def test_judge_prompt_never_leaks_real_model_names(self):
        prompt = llm_battle.build_judge_prompt(
            dna={"reader_archetype": "Test", "taste_summary": "", "top_themes": [], "avoid_themes": [], "taste_dimensions": {}},
            recs=[{"title": "A Book", "author": "An Author", "why": "reasons"}],
            recommender_label="Recommender B",
        )
        self.assertNotIn("GPT-OSS", prompt)
        self.assertNotIn("GLM", prompt)
        self.assertIn("Recommender B", prompt)


if __name__ == "__main__":
    unittest.main()
