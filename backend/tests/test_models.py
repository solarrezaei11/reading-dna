"""Strict request and LLM-output model validation."""
import unittest

from pydantic import ValidationError

from models import (
    BattleResultsPayload,
    Book,
    DnaProfile,
    JudgeVerdictPayload,
    MapRecommendation,
    PredictRequest,
    RecommendationItem,
)


class StrictModelTests(unittest.TestCase):
    VALID_DNA = {
        "reader_archetype": "Reader",
        "one_liner": "A complete reader.",
        "taste_dimensions": {
            "prose_density": 5,
            "pacing_preference": 5,
            "fiction_ratio": 50,
            "intellectual_depth": 5,
            "emotional_intensity": 5,
            "contrarian_score": None,
        },
        "top_themes": [],
        "avoid_themes": [],
        "favorite_authors": [],
        "taste_summary": "Complete summary.",
        "blind_spot_genres": [],
        "top_books": [],
        "total_books": 1,
        "avg_rating": 4.0,
    }

    def test_book_rejects_coerced_numeric_strings(self):
        with self.assertRaises(ValidationError):
            Book(title="Book", my_rating="5")

    def test_book_rejects_non_string_genres(self):
        with self.assertRaises(ValidationError):
            Book(title="Book", genres=["Fiction", 7])

    def test_dna_profile_rejects_unknown_fields(self):
        payload = {**self.VALID_DNA, "untrusted_extra": True}
        with self.assertRaises(ValidationError):
            DnaProfile.model_validate(payload)

    def test_dna_profile_rejects_incomplete_llm_output(self):
        with self.assertRaises(ValidationError):
            DnaProfile.model_validate({"reader_archetype": "Reader", "taste_dimensions": {}})

    def test_dna_profile_normalizes_null_top_book_isbn(self):
        payload = {
            **self.VALID_DNA,
            "top_books": [
                {
                    "title": "A Book",
                    "author": "An Author",
                    "why_loved": "It fits.",
                    "isbn": None,
                }
            ],
        }
        profile = DnaProfile.model_validate(payload)
        self.assertEqual(profile.top_books[0].isbn, "")

    def test_recommendation_rejects_unknown_fields(self):
        with self.assertRaises(ValidationError):
            RecommendationItem.model_validate({"title": "Book", "surprise": "value"})

    def test_map_recommendation_is_typed_and_bounded(self):
        recommendation = MapRecommendation.model_validate(
            {"title": " Book ", "author": " Author ", "genres": [" Fiction "]}
        )
        self.assertEqual(recommendation.title, "Book")
        self.assertEqual(recommendation.author, "Author")
        self.assertEqual(recommendation.genres, ["Fiction"])

        with self.assertRaises(ValidationError):
            MapRecommendation.model_validate({"title": "Book", "unexpected": True})

    def test_judge_scores_reject_string_coercion(self):
        with self.assertRaises(ValidationError):
            JudgeVerdictPayload.model_validate(
                {"scores": {"relevance": "7"}, "verdict": "No coercion"}
            )

    def test_predict_request_requires_typed_dna_profile(self):
        with self.assertRaises(ValidationError):
            PredictRequest.model_validate({"title": "Book", "dna_profile": {}})

    def test_judge_battle_payload_accepts_known_models_and_rejects_unknown(self):
        def model_payload(display):
            return {
                "recommendations": [{"title": f"{display} Book", "author": "Author"}],
                "meta": None,
                "info": {
                    "display": display,
                    "description": "Model description.",
                    "architecture": "MoE",
                    "total_params": "1B",
                    "active_params": "1B",
                    "task_fit": "general",
                },
            }

        from providers import KNOWN_MODEL_DISPLAYS

        # N-way: any subset of registered provider models is accepted, not a
        # fixed pair — including the Groq/OpenRouter free-tier competitors.
        known = sorted(KNOWN_MODEL_DISPLAYS)
        self.assertGreaterEqual(len(known), 3)
        payload = {"models": {display: model_payload(display) for display in known}}
        validated = BattleResultsPayload.model_validate(payload)
        self.assertEqual(set(validated.models), set(known))

        # An unknown/fabricated model name is rejected outright.
        with self.assertRaises(ValidationError):
            BattleResultsPayload.model_validate(
                {"models": {"Totally Made Up Model": model_payload("Totally Made Up Model")}}
            )

        # An empty roster is rejected.
        with self.assertRaises(ValidationError):
            BattleResultsPayload.model_validate({"models": {}})

        # A structurally-invalid payload for a known model still fails.
        malformed = {"models": {**payload["models"], known[0]: []}}
        with self.assertRaises(ValidationError):
            BattleResultsPayload.model_validate(malformed)


if __name__ == "__main__":
    unittest.main()
