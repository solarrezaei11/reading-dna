"""Text embeddings, dimensionality reduction, and clustering for the reading map.

Notable correctness fixes:
  - The sentence-transformer model is loaded off the event loop
    (asyncio.to_thread) behind an asyncio.Lock, so the first request doesn't
    block the loop and concurrent first-requests don't race to load it twice.
  - StandardScaler/UMAP/KMeans (all CPU-bound, synchronous) run in a thread
    via asyncio.to_thread, bounded by a semaphore so a burst of requests
    can't spin up unbounded CPU-bound work in parallel.
  - The 2D "reference space" (UMAP fit) is fit ONLY on the user's books +
    fixed genre anchors. Recommendations are transformed INTO that fixed
    space afterward, so generating recommendations never moves the user's
    own map around.
  - Clustering runs on L2-normalized ORIGINAL book embeddings (384-dim),
    not the lossy 2D UMAP projection, and both UMAP and the normalization
    are cosine-appropriate (UMAP metric="cosine" over L2-normalized vectors).
  - An in-memory, bounded LRU cache keyed by text content means an unchanged
    shelf isn't re-embedded on every /predict or /embeddings call.
"""
import asyncio
import hashlib
import logging
from collections import OrderedDict
from typing import Optional

import numpy as np

from config import CPU_ANALYSIS_CONCURRENCY, EMBEDDING_CACHE_MAX_ENTRIES
from error_safety import safe_exception_summary
from llm_client import call_with_limit
from prompt_safety import guarded_system_prompt, sanitize_for_prompt

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 output size

_model = None
_model_lock = asyncio.Lock()
_cpu_semaphore = asyncio.Semaphore(CPU_ANALYSIS_CONCURRENCY)
_cerebras_client = None

# Fixed genre anchors — embedded alongside user books so positions are meaningful
GENRE_ANCHORS = [
    ("Literary Fiction", "Dense literary fiction with complex characters, beautiful prose, and emotional depth"),
    ("Science Fiction", "Speculative science fiction exploring technology, AI, and future worlds"),
    ("Fantasy", "Epic fantasy with world-building, magic systems, and adventure"),
    ("Thriller & Mystery", "Fast-paced thrillers with suspense, crime, and mystery"),
    ("Historical Fiction", "Historical novels set in the past with rich period detail"),
    ("Biography & Memoir", "True life stories, autobiographies, and personal memoirs"),
    ("Self-Help", "Personal development, productivity, habits, and self-improvement"),
    ("Business & Economics", "Business strategy, entrepreneurship, startups, and economic theory"),
    ("Philosophy & Ideas", "Philosophy, ethics, meaning, and big ideas about existence"),
    ("Popular Science", "Popular science, natural history, physics, and scientific discovery"),
    ("Psychology", "Human psychology, behavior, cognitive science, and mental health"),
    ("Politics & Society", "Politics, social movements, power structures, and sociology"),
    ("Dystopian Fiction", "Dystopian and post-apocalyptic fiction about society and control"),
    ("Romance", "Romantic fiction and contemporary love stories"),
    ("Horror", "Horror, dark fiction, and psychological terror"),
]


# ---------------------------------------------------------------------------
# Model loading (off the event loop, concurrency-safe)
# ---------------------------------------------------------------------------

async def _get_model():
    global _model
    if _model is not None:
        return _model
    async with _model_lock:
        if _model is None:  # re-check: another task may have loaded it while we waited
            def _load():
                from sentence_transformers import SentenceTransformer

                return SentenceTransformer("all-MiniLM-L6-v2")

            _model = await asyncio.to_thread(_load)
    return _model


def _get_cerebras_client():
    global _cerebras_client
    if _cerebras_client is None:
        import os

        from cerebras.cloud.sdk import AsyncCerebras

        _cerebras_client = AsyncCerebras(api_key=os.environ.get("CEREBRAS_API_KEY"))
    return _cerebras_client


# ---------------------------------------------------------------------------
# Bounded embedding cache
# ---------------------------------------------------------------------------

class _LRUEmbeddingCache:
    """Bounded in-memory cache keyed by a hash of the embedded text, so an
    unchanged shelf/library doesn't get re-embedded on every request."""

    def __init__(self, max_entries: int = EMBEDDING_CACHE_MAX_ENTRIES):
        self.max_entries = max_entries
        self._store: "OrderedDict[str, np.ndarray]" = OrderedDict()
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def get_many(self, texts: list[str]) -> tuple[list[Optional[np.ndarray]], list[int]]:
        async with self._lock:
            results: list[Optional[np.ndarray]] = []
            missing: list[int] = []
            for i, t in enumerate(texts):
                key = self._key(t)
                if key in self._store:
                    self._store.move_to_end(key)
                    results.append(self._store[key])
                else:
                    results.append(None)
                    missing.append(i)
            return results, missing

    async def put_many(self, texts: list[str], vectors) -> None:
        async with self._lock:
            for t, v in zip(texts, vectors):
                key = self._key(t)
                self._store[key] = np.asarray(v)
                self._store.move_to_end(key)
                while len(self._store) > self.max_entries:
                    self._store.popitem(last=False)


_embedding_cache = _LRUEmbeddingCache()


async def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed `texts` off the event loop, reusing cached vectors for any text
    seen before so repeated calls with an unchanged shelf are near-instant."""
    if not texts:
        return np.zeros((0, EMBEDDING_DIM))

    cached, missing_idx = await _embedding_cache.get_many(texts)

    if missing_idx:
        model = await _get_model()
        to_embed = [texts[i] for i in missing_idx]
        async with _cpu_semaphore:
            new_vectors = await asyncio.to_thread(model.encode, to_embed, show_progress_bar=False)
        await _embedding_cache.put_many(to_embed, new_vectors)
        for pos, i in enumerate(missing_idx):
            cached[i] = np.asarray(new_vectors[pos])

    return np.vstack(cached)


def book_to_text(b: dict) -> str:
    parts = [b["title"], f"by {b['author']}"]
    if b.get("genres"):
        parts.append(", ".join(b["genres"]))
    if b.get("my_review"):
        parts.append(b["my_review"][:150])
    return " | ".join(parts)


def _l2_normalize(vecs: np.ndarray) -> np.ndarray:
    if len(vecs) == 0:
        return vecs
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.clip(norms, 1e-8, None)


def _build_cluster_naming_prompt(clusters: dict[int, list[dict]]) -> str:
    """Extracted for direct testability. Book titles come from the user's
    own (untrusted, Goodreads-sourced) shelf data, so they are sanitized
    before being embedded in the prompt."""
    cluster_descriptions = {
        cid: ", ".join(f'"{sanitize_for_prompt(b.get("title", ""))}"' for b in books[:6])
        for cid, books in clusters.items()
    }
    return f"""Name each book cluster with a short thematic label (3-5 words). Capture the shared genre, mood, or intellectual territory.

{chr(10).join(f'Cluster {cid}: {desc}' for cid, desc in cluster_descriptions.items())}

Return ONLY valid JSON: {{{", ".join(f'"{cid}": "label"' for cid in clusters)}}}"""


CLUSTER_NAMING_SYSTEM_PROMPT = guarded_system_prompt(
    "You are a literary analyst. Return only valid JSON."
)


async def name_clusters_with_llm(clusters: dict[int, list[dict]]) -> tuple[dict[int, str], Optional[str]]:
    """Ask the LLM for short thematic cluster labels.

    Returns (names, warning). `warning` is non-None whenever the LLM call
    failed, timed out, or returned an invalid/incomplete set of names (wrong
    cluster count, non-string/empty labels) — the fallback title-based
    labels are still usable, just lower quality, and callers should surface
    this warning to the user rather than only logging it.
    """
    prompt = _build_cluster_naming_prompt(clusters)

    fallback = {cid: books[0]["title"].split(":")[0][:25] for cid, books in clusters.items()}

    try:
        import json

        client = _get_cerebras_client()
        resp = await call_with_limit(
            client.chat.completions.create(
                model="gpt-oss-120b",
                messages=[
                    {"role": "system", "content": CLUSTER_NAMING_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
            )
        )
        text = resp.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        raw = json.loads(text.strip())
        if not isinstance(raw, dict):
            raise ValueError(f"cluster-name response must be a JSON object, got {type(raw).__name__}")
        names = {int(k): v for k, v in raw.items()}

        expected_ids = set(clusters)
        if set(names) != expected_ids:
            raise ValueError(f"cluster-name response covers {sorted(names)}, expected {sorted(expected_ids)}")
        if not all(isinstance(v, str) and v.strip() for v in names.values()):
            raise ValueError("cluster-name response contains a non-string or empty label")

        return {cid: v.strip() for cid, v in names.items()}, None
    except asyncio.CancelledError:
        raise  # never swallow cancellation
    except Exception as e:
        # Explicit, visible fallback — never silently swallow this. A missing
        # API key, network error, timeout, or malformed/invalid LLM response
        # (wrong cluster count, non-string labels) all land here, and the
        # caller is told about it via the returned warning, not just a log line.
        warning = (
            f"Cluster naming via LLM failed ({safe_exception_summary(e)}); "
            "using fallback title-based labels."
        )
        logger.warning(warning)
        return fallback, warning


# ---------------------------------------------------------------------------
# CPU-bound work off the event loop
# ---------------------------------------------------------------------------

async def _fit_reference_umap(normalized_ref: np.ndarray, n_neighbors: int):
    def _fit():
        import umap

        reducer = umap.UMAP(
            n_components=2,
            n_neighbors=n_neighbors,
            min_dist=0.35,
            spread=1.8,
            metric="cosine",  # cosine-appropriate: these are L2-normalized semantic embeddings
            random_state=42,  # deterministic layout across runs
        )
        coords = reducer.fit_transform(normalized_ref)
        return reducer, coords

    async with _cpu_semaphore:
        return await asyncio.to_thread(_fit)


async def _transform_umap(reducer, normalized_points: np.ndarray) -> np.ndarray:
    async with _cpu_semaphore:
        return await asyncio.to_thread(reducer.transform, normalized_points)


async def _fit_kmeans(normalized_book_embs: np.ndarray, n_clusters: int) -> list[int]:
    def _fit():
        from sklearn.cluster import KMeans

        km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = km.fit_predict(normalized_book_embs)
        return [int(l) for l in labels]

    async with _cpu_semaphore:
        return await asyncio.to_thread(_fit)


async def generate_embeddings_and_umap(books: list[dict], recommendations: Optional[list[dict]] = None) -> dict:
    recommendations = recommendations or []
    if not books:
        return {"points": [], "genre_anchors": [], "rec_points": [], "warnings": []}

    # Sort books deterministically so UMAP layout is stable across runs
    # (Goodreads RSS order varies between requests, which shifts UMAP output)
    books = sorted(books, key=lambda b: (b.get("title", "").lower(), b.get("author", "").lower()))
    recommendations = sorted(recommendations, key=lambda r: (r.get("title", "").lower(), r.get("model_name", "")))

    book_texts = [book_to_text(b) for b in books]
    anchor_texts = [desc for _, desc in GENRE_ANCHORS]
    rec_texts = [book_to_text(r) for r in recommendations]

    all_texts = book_texts + anchor_texts + rec_texts
    all_embeddings = await embed_texts(all_texts)

    n_books = len(books)
    n_anchors = len(GENRE_ANCHORS)

    book_embs = np.asarray(all_embeddings[:n_books])
    anchor_embs = np.asarray(all_embeddings[n_books:n_books + n_anchors])
    rec_embs = (
        np.asarray(all_embeddings[n_books + n_anchors:])
        if rec_texts
        else np.zeros((0, all_embeddings.shape[1]))
    )

    book_embs_norm = _l2_normalize(book_embs)
    anchor_embs_norm = _l2_normalize(anchor_embs)
    rec_embs_norm = _l2_normalize(rec_embs)

    # Fit the STABLE reference space on user books + genre anchors ONLY, so
    # generating recommendations never moves the user's own map.
    reference = np.vstack([book_embs_norm, anchor_embs_norm])
    n_neighbors = max(2, min(12, len(reference) - 1))
    reducer, ref_coords = await _fit_reference_umap(reference, n_neighbors)

    # Recommendations are transformed INTO the fixed reference space, not fit alongside it.
    rec_coords = np.zeros((0, 2))
    if len(rec_embs_norm):
        rec_coords = await _transform_umap(reducer, rec_embs_norm)

    # Normalize to [0,1] using the reference space's own bounds — recommendation
    # points are allowed to fall outside [0,1] rather than stretching the user's map.
    xs, ys = ref_coords[:, 0], ref_coords[:, 1]
    x_min, x_max = xs.min(), xs.max()
    y_min, y_max = ys.min(), ys.max()

    def norm_x(v):
        return float((v - x_min) / (x_max - x_min + 1e-8))

    def norm_y(v):
        return float((v - y_min) / (y_max - y_min + 1e-8))

    book_coords = ref_coords[:n_books]
    anchor_coords = ref_coords[n_books:n_books + n_anchors]

    # Cluster the L2-normalized ORIGINAL book embeddings (full 384-dim semantic
    # content), not the 2D UMAP projection — UMAP discards information that's
    # useful for clustering even though it's good for visualization.
    n_clusters = max(2, min(5, n_books // 6))
    n_clusters = min(n_clusters, n_books)
    cluster_ids = await _fit_kmeans(book_embs_norm, n_clusters)

    clusters: dict[int, list[dict]] = {}
    for i, cid in enumerate(cluster_ids):
        clusters.setdefault(cid, []).append(books[i])
    for cid in clusters:
        clusters[cid].sort(key=lambda b: (-(b.get("my_rating", 0) or 0), b.get("title", "").lower()))

    cluster_names, cluster_name_warning = await name_clusters_with_llm(clusters)

    points = []
    for i, book in enumerate(books):
        cid = cluster_ids[i]
        points.append({
            **book,
            "x": norm_x(book_coords[i][0]),
            "y": norm_y(book_coords[i][1]),
            "cluster_id": cid,
            "cluster_name": cluster_names.get(cid, f"Group {cid + 1}"),
            "point_type": "read",
        })

    genre_anchors = []
    for i, (name, _) in enumerate(GENRE_ANCHORS):
        c = anchor_coords[i]
        ax, ay = norm_x(c[0]), norm_y(c[1])
        nearest_dist = min(
            ((p["x"] - ax) ** 2 + (p["y"] - ay) ** 2) ** 0.5
            for p in points
        ) if points else 1.0
        genre_anchors.append({
            "name": name,
            "x": ax,
            "y": ay,
            "explored": nearest_dist < 0.2,  # user has books in this zone
        })

    rec_points = []
    for i, rec in enumerate(recommendations):
        if i < len(rec_coords):
            rec_points.append({
                **rec,
                "x": norm_x(rec_coords[i][0]),
                "y": norm_y(rec_coords[i][1]),
                "point_type": "recommendation",
            })

    return {
        "points": points,
        "genre_anchors": genre_anchors,
        "rec_points": rec_points,
        "cluster_names": cluster_names,
        "warnings": [cluster_name_warning] if cluster_name_warning else [],
    }
