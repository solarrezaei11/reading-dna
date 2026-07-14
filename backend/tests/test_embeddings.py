"""Tests for embeddings.name_clusters_with_llm (invalid-shape detection,
explicit fallback warnings, cancellation safety) and the propagation of its
warning into generate_embeddings_and_umap's returned "warnings" list.

embeddings.py lazily imports sentence-transformers/umap/sklearn/cerebras
only inside functions that need them, so these tests mock at the function
boundary (_get_cerebras_client, embed_texts, the CPU-bound helpers) rather
than requiring those heavy dependencies to be installed/exercised.
"""
import asyncio
import unittest
from unittest import mock
from types import SimpleNamespace

import numpy as np

import embeddings


def _make_clusters():
    return {
        0: [{"title": "Book A", "author": "Author A", "my_rating": 5}],
        1: [{"title": "Book B", "author": "Author B", "my_rating": 4}],
    }


class FakeCompletions:
    def __init__(self, content):
        self._content = content

    async def create(self, **kwargs):
        message = SimpleNamespace(content=self._content)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])


def make_fake_client(content):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(content)))


class NameClustersWithLlmTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_response_returns_names_with_no_warning(self):
        clusters = _make_clusters()
        fake_client = make_fake_client('{"0": "Cozy Character Studies", "1": "Fast-Paced Thrillers"}')

        with mock.patch.object(embeddings, "_get_cerebras_client", return_value=fake_client):
            names, warning = await embeddings.name_clusters_with_llm(clusters)

        self.assertIsNone(warning)
        self.assertEqual(names[0], "Cozy Character Studies")
        self.assertEqual(names[1], "Fast-Paced Thrillers")

    async def test_llm_exception_falls_back_with_explicit_warning(self):
        clusters = _make_clusters()

        class RaisingCompletions:
            async def create(self, **kwargs):
                raise RuntimeError("cerebras unavailable")

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=RaisingCompletions()))

        with mock.patch.object(embeddings, "_get_cerebras_client", return_value=fake_client):
            names, warning = await embeddings.name_clusters_with_llm(clusters)

        self.assertIsNotNone(warning)
        self.assertEqual(set(names), {0, 1})  # fallback still covers every cluster
        self.assertIn("fallback", warning.lower())

    async def test_wrong_cluster_count_in_response_falls_back_with_warning(self):
        clusters = _make_clusters()
        # Only names cluster 0, missing cluster 1 entirely.
        fake_client = make_fake_client('{"0": "Only One Label"}')

        with mock.patch.object(embeddings, "_get_cerebras_client", return_value=fake_client):
            names, warning = await embeddings.name_clusters_with_llm(clusters)

        self.assertIsNotNone(warning)
        self.assertEqual(set(names), {0, 1})

    async def test_non_string_label_falls_back_with_warning(self):
        clusters = _make_clusters()
        fake_client = make_fake_client('{"0": 123, "1": "Valid Label"}')

        with mock.patch.object(embeddings, "_get_cerebras_client", return_value=fake_client):
            names, warning = await embeddings.name_clusters_with_llm(clusters)

        self.assertIsNotNone(warning)

    async def test_empty_string_label_falls_back_with_warning(self):
        clusters = _make_clusters()
        fake_client = make_fake_client('{"0": "", "1": "Valid Label"}')

        with mock.patch.object(embeddings, "_get_cerebras_client", return_value=fake_client):
            names, warning = await embeddings.name_clusters_with_llm(clusters)

        self.assertIsNotNone(warning)

    async def test_non_object_json_falls_back_with_warning(self):
        clusters = _make_clusters()
        fake_client = make_fake_client('["not", "an", "object"]')

        with mock.patch.object(embeddings, "_get_cerebras_client", return_value=fake_client):
            names, warning = await embeddings.name_clusters_with_llm(clusters)

        self.assertIsNotNone(warning)
        self.assertEqual(set(names), {0, 1})

    async def test_cancelled_error_is_never_swallowed(self):
        clusters = _make_clusters()

        class CancellingCompletions:
            async def create(self, **kwargs):
                raise asyncio.CancelledError()

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=CancellingCompletions()))

        with mock.patch.object(embeddings, "_get_cerebras_client", return_value=fake_client):
            with self.assertRaises(asyncio.CancelledError):
                await embeddings.name_clusters_with_llm(clusters)


class GenerateEmbeddingsWarningsPropagationTests(unittest.IsolatedAsyncioTestCase):
    """Exercise generate_embeddings_and_umap end-to-end with the heavy
    CPU-bound / embedding steps stubbed out, to prove the cluster-naming
    warning from name_clusters_with_llm surfaces in the endpoint-level
    "warnings" list rather than only being logged."""

    def _books(self, n=6):
        return [{"title": f"Book {i}", "author": f"Author {i}", "my_rating": (i % 5) + 1} for i in range(n)]

    async def test_cluster_name_fallback_warning_propagates_to_top_level_warnings(self):
        books = self._books()

        async def fake_embed_texts(texts):
            # One row per text, arbitrary but distinguishable low-dim vectors.
            return np.random.RandomState(0).rand(len(texts), 8)

        async def fake_fit_reference_umap(normalized_ref, n_neighbors):
            coords = np.random.RandomState(1).rand(len(normalized_ref), 2)
            return SimpleNamespace(transform=lambda pts: np.zeros((len(pts), 2))), coords

        async def fake_fit_kmeans(normalized_book_embs, n_clusters):
            return [i % n_clusters for i in range(len(normalized_book_embs))]

        async def fake_name_clusters(clusters):
            return {cid: f"Fallback {cid}" for cid in clusters}, "Cluster naming via LLM failed (boom); using fallback title-based labels."

        with mock.patch.object(embeddings, "embed_texts", side_effect=fake_embed_texts), \
             mock.patch.object(embeddings, "_fit_reference_umap", side_effect=fake_fit_reference_umap), \
             mock.patch.object(embeddings, "_fit_kmeans", side_effect=fake_fit_kmeans), \
             mock.patch.object(embeddings, "name_clusters_with_llm", side_effect=fake_name_clusters):
            result = await embeddings.generate_embeddings_and_umap(books)

        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("fallback", result["warnings"][0].lower())
        self.assertEqual(len(result["points"]), len(books))

    async def test_no_warning_when_cluster_naming_succeeds(self):
        books = self._books()

        async def fake_embed_texts(texts):
            return np.random.RandomState(0).rand(len(texts), 8)

        async def fake_fit_reference_umap(normalized_ref, n_neighbors):
            coords = np.random.RandomState(1).rand(len(normalized_ref), 2)
            return SimpleNamespace(transform=lambda pts: np.zeros((len(pts), 2))), coords

        async def fake_fit_kmeans(normalized_book_embs, n_clusters):
            return [i % n_clusters for i in range(len(normalized_book_embs))]

        async def fake_name_clusters(clusters):
            return {cid: f"Real Label {cid}" for cid in clusters}, None

        with mock.patch.object(embeddings, "embed_texts", side_effect=fake_embed_texts), \
             mock.patch.object(embeddings, "_fit_reference_umap", side_effect=fake_fit_reference_umap), \
             mock.patch.object(embeddings, "_fit_kmeans", side_effect=fake_fit_kmeans), \
             mock.patch.object(embeddings, "name_clusters_with_llm", side_effect=fake_name_clusters):
            result = await embeddings.generate_embeddings_and_umap(books)

        self.assertEqual(result["warnings"], [])

    async def test_empty_books_returns_empty_shape_with_no_warnings(self):
        result = await embeddings.generate_embeddings_and_umap([])
        self.assertEqual(result, {"points": [], "genre_anchors": [], "rec_points": [], "warnings": []})


if __name__ == "__main__":
    unittest.main()
