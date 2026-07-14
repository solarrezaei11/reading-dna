"""Deterministic, representative book sampling — shared by dna.py (Reading
DNA profile generation) and llm_battle.py (battle recommendation prompts).

Extracted into its own module (rather than one importing from the other) so
dna.py and llm_battle.py never need to import each other: both import this
module instead, avoiding a circular import between them.

Why this exists: showing an LLM only the highest-rated N books (or the first
N in whatever order a shelf happened to arrive) biases every downstream
judgment — the reading DNA profile, and now the battle recommendations —
toward "what they loved most recently" instead of a representative picture
of the reader's actual taste. This module stratifies samples across rating
buckets (1-5 stars) and, within each bucket, spreads picks across recency so
old and recent reads are both represented.
"""

from prompt_safety import sanitize_for_prompt

DEFAULT_SAMPLE_TARGET = 80


def _recency_sort_key(b: dict):
    """Sort by recency (most recent read year first, unknowns last), then a
    canonical (title, author) tie-break so ordering is fully deterministic."""
    year = b.get("year_read")
    return (0 if year is not None else 1, -(year or 0), (b.get("title") or "").lower(), (b.get("author") or "").lower())


def _final_sort_key(b: dict):
    """Canonical output ordering: highest rating first, then recency, then title/author."""
    year = b.get("year_read")
    return (
        -(b.get("my_rating") or 0),
        0 if year is not None else 1,
        -(year or 0),
        (b.get("title") or "").lower(),
        (b.get("author") or "").lower(),
    )


def _allocate_bucket_counts(bucket_sizes: dict[int, int], target: int) -> dict[int, int]:
    """Proportionally allocate `target` slots across rating buckets 1-5,
    giving every non-empty bucket a slot when the target is large enough,
    and adjusting deterministically so the allocation sums exactly to the
    smaller of `target` and the available book count."""
    total = sum(bucket_sizes.values())
    target = max(0, min(target, total))
    populated_count = sum(size > 0 for size in bucket_sizes.values())
    minimum = 1 if target >= populated_count else 0
    allocations: dict[int, int] = {}
    for rating in sorted(bucket_sizes):
        n = bucket_sizes[rating]
        allocations[rating] = max(minimum, round(target * n / total)) if n else 0

    # Never allocate more than a bucket actually has.
    for rating in allocations:
        allocations[rating] = min(allocations[rating], bucket_sizes[rating])

    diff = target - sum(allocations.values())
    # Adjust deterministically, largest buckets first, until the total matches.
    order = sorted(bucket_sizes, key=lambda r: (-bucket_sizes[r], r))
    guard = 0
    while diff != 0 and order and guard < 10_000:
        for rating in order:
            if diff == 0:
                break
            if diff > 0 and allocations[rating] < bucket_sizes[rating]:
                allocations[rating] += 1
                diff -= 1
            elif diff < 0 and allocations[rating] > (minimum if bucket_sizes[rating] else 0):
                allocations[rating] -= 1
                diff += 1
        guard += 1
    return allocations


def build_representative_sample(books: list[dict], target: int = DEFAULT_SAMPLE_TARGET) -> list[dict]:
    """Deterministically sample up to `target` books, stratified across
    rating buckets (so low ratings aren't drowned out by 5-star books) and,
    within each bucket, spread across recency (so the sample isn't just the
    most recently read or the first N in whatever order the shelf arrived)."""
    buckets: dict[int, list[dict]] = {r: [] for r in range(1, 6)}
    for b in books:
        rating = b.get("my_rating", 0)
        if 1 <= rating <= 5:
            buckets[rating].append(b)

    total = sum(len(v) for v in buckets.values())
    if total == 0 or target <= 0:
        return []
    if total <= target:
        # Only completed books with an explicit 1-5 rating are evidence for
        # taste inference. Unrated entries must not bypass the sample cap or
        # appear as artificial zero-star dislikes.
        sampled = [book for rating in range(1, 6) for book in buckets[rating]]
    else:
        allocations = _allocate_bucket_counts({r: len(v) for r, v in buckets.items()}, target)
        sampled = []
        for rating, bucket_books in buckets.items():
            alloc = allocations.get(rating, 0)
            if alloc <= 0 or not bucket_books:
                continue
            ordered = sorted(bucket_books, key=_recency_sort_key)
            n = len(ordered)
            if alloc >= n:
                sampled.extend(ordered)
                continue
            # Evenly spaced indices across the recency-ordered list, so the
            # sample spans old and recent reads instead of clustering at one end.
            step = n / alloc
            indices = sorted({int(i * step) for i in range(alloc)})
            idx_set = set(indices)
            i = 0
            while len(indices) < alloc and i < n:
                if i not in idx_set:
                    indices.append(i)
                    idx_set.add(i)
                i += 1
            sampled.extend(ordered[i] for i in sorted(indices)[:alloc])

    return sorted(sampled, key=_final_sort_key)


def format_book_line(b: dict, review_chars: int) -> str:
    """Render one book as a single descriptive line for an LLM prompt:
    rating, Goodreads average rating (when present), title/author/year, and
    a bounded review excerpt (when present).

    Title, author, and review text are untrusted (sourced from Goodreads)
    and are run through sanitize_for_prompt() before being embedded: control
    characters are stripped and embedded newlines/tabs are collapsed to a
    single space so a review can't fabricate a fake extra line that looks
    like a new record or a new prompt section. Ordinary Unicode book text is
    left completely untouched.
    """
    avg = b.get("avg_rating") or 0
    avg_part = f", GR avg {avg:.2f}" if avg else ""
    title = sanitize_for_prompt(b.get("title", ""))
    author = sanitize_for_prompt(b.get("author", ""))
    review = sanitize_for_prompt(b.get("my_review", ""), review_chars)
    review_part = f' — "{review}"' if review else ""
    year = sanitize_for_prompt(str(b.get("year_published") or "")) or "?"
    return f'{b.get("my_rating", 0)}/5{avg_part} — "{title}" by {author} ({year}){review_part}'


def build_book_summary(books: list[dict], target: int = DEFAULT_SAMPLE_TARGET, review_chars: int = 200) -> str:
    """Build a newline-separated, LLM-prompt-ready summary of a representative
    sample of `books` (see build_representative_sample)."""
    sample = build_representative_sample(books, target)
    return "\n".join(format_book_line(b, review_chars) for b in sample)
