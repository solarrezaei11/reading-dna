import asyncio
import json
import os
import numpy as np
import umap
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sentence_transformers import SentenceTransformer
from cerebras.cloud.sdk import AsyncCerebras

_model = None
cerebras = AsyncCerebras(api_key=os.environ.get("CEREBRAS_API_KEY"))

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


def get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


async def embed_texts(texts: list[str]) -> np.ndarray:
    model = get_model()
    return await asyncio.to_thread(model.encode, texts, show_progress_bar=False)


def book_to_text(b: dict) -> str:
    parts = [b["title"], f"by {b['author']}"]
    if b.get("genres"):
        parts.append(", ".join(b["genres"]))
    if b.get("my_review"):
        parts.append(b["my_review"][:150])
    return " | ".join(parts)


async def name_clusters_with_llm(clusters: dict[int, list[dict]]) -> dict[int, str]:
    cluster_descriptions = {
        cid: ", ".join(f'"{b["title"]}"' for b in books[:6])
        for cid, books in clusters.items()
    }
    prompt = f"""Name each book cluster with a short thematic label (3-5 words). Capture the shared genre, mood, or intellectual territory.

{chr(10).join(f'Cluster {cid}: {desc}' for cid, desc in cluster_descriptions.items())}

Return ONLY valid JSON: {{{", ".join(f'"{cid}": "label"' for cid in clusters)}}}"""

    try:
        resp = await cerebras.chat.completions.create(
            model="gpt-oss-120b",
            messages=[
                {"role": "system", "content": "You are a literary analyst. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        text = resp.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return {int(k): v for k, v in json.loads(text.strip()).items()}
    except Exception:
        return {cid: books[0]["title"].split(":")[0][:25] for cid, books in clusters.items()}


async def generate_embeddings_and_umap(books: list[dict], recommendations: list[dict] = []) -> dict:
    if not books:
        return {"points": [], "genre_anchors": [], "rec_points": []}

    # Sort books deterministically so UMAP layout is stable across runs
    # (Goodreads RSS order varies between requests, which shifts UMAP output)
    books = sorted(books, key=lambda b: (b.get("title", "").lower(), b.get("author", "").lower()))
    recommendations = sorted(recommendations, key=lambda r: (r.get("title", "").lower(), r.get("model_name", "")))

    # Build text for all point types
    book_texts = [book_to_text(b) for b in books]
    anchor_texts = [desc for _, desc in GENRE_ANCHORS]
    rec_texts = [book_to_text(r) for r in recommendations]

    all_texts = book_texts + anchor_texts + rec_texts
    all_embeddings = await embed_texts(all_texts)

    n_books = len(books)
    n_anchors = len(GENRE_ANCHORS)

    book_embs = all_embeddings[:n_books]
    anchor_embs = all_embeddings[n_books:n_books + n_anchors]
    rec_embs = all_embeddings[n_books + n_anchors:] if rec_texts else np.array([]).reshape(0, all_embeddings.shape[1])

    # Fit UMAP on everything together so positions are comparable
    combined = np.vstack([book_embs, anchor_embs] + ([rec_embs] if len(rec_embs) else []))
    scaled = StandardScaler().fit_transform(combined)

    n_total = len(scaled)
    n_neighbors = min(12, n_total - 1)
    reducer = umap.UMAP(n_components=2, n_neighbors=n_neighbors, min_dist=0.35, spread=1.8, random_state=42)
    coords = reducer.fit_transform(scaled)

    # Normalize all to [0,1]
    xs, ys = coords[:, 0], coords[:, 1]
    x_min, x_max = xs.min(), xs.max()
    y_min, y_max = ys.min(), ys.max()
    def norm_x(v): return float((v - x_min) / (x_max - x_min + 1e-8))
    def norm_y(v): return float((v - y_min) / (y_max - y_min + 1e-8))

    book_coords = coords[:n_books]
    anchor_coords = coords[n_books:n_books + n_anchors]
    rec_coords = coords[n_books + n_anchors:] if len(rec_embs) else []

    # Cluster user books
    n_clusters = max(2, min(5, n_books // 6))
    bx = np.array([[norm_x(c[0]), norm_y(c[1])] for c in book_coords])
    km = KMeans(n_clusters=min(n_clusters, n_books), random_state=42, n_init=10)
    cluster_ids = [int(l) for l in km.fit_predict(bx)]

    clusters: dict[int, list[dict]] = {}
    for i, cid in enumerate(cluster_ids):
        clusters.setdefault(cid, []).append(books[i])
    for cid in clusters:
        clusters[cid].sort(key=lambda b: b.get("my_rating", 0), reverse=True)

    cluster_names = await name_clusters_with_llm(clusters)

    # Build output
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
        # Check if user has books near this anchor
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
    }
