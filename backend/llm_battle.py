"""Dual-model book recommendation "battle" + a local judge model.

Notable correctness fixes this module implements:
  - TTFT (time-to-first-token) is measured with time.perf_counter() from the
    first non-empty *content* delta, not the first stream event (which may
    be a role-only delta, an empty chunk, or a usage-only chunk).
  - Per-attempt timeouts are bounded (LLM_ATTEMPT_TIMEOUT_SECONDS) and
    implemented with asyncio.wait_for, which cancels the underlying request
    cleanly rather than leaving it dangling.
  - The RUBRIC shown to users and the dimensions the judge actually scores
    are the same set of keys, so "how we grade" and "what the judge grades"
    can't silently drift apart.
  - The judge is blinded to model identity (recommenders are labeled A/B in
    a randomized mapping) to avoid name-based bias, and ties/errors are
    reported explicitly rather than papered over.
"""
import asyncio
import json
import logging
import random
import re
import time
import unicodedata
from typing import Optional

import httpx
from pydantic import ValidationError

from config import ISBN_VERIFY_CONCURRENCY, ISBN_VERIFY_TIMEOUT_SECONDS, LLM_ATTEMPT_TIMEOUT_SECONDS, MAX_REVIEW_EXCERPT_CHARS
from error_safety import safe_exception_summary, validation_error_summary
from llm_client import call_with_limit
from models import JudgeVerdictPayload, RecommendationItem
from open_library import lookup_open_library
from prompt_safety import guarded_system_prompt, sanitize_for_prompt
from sampling import build_representative_sample, format_book_line

logger = logging.getLogger(__name__)

MODEL_INFO = {
    "gpt-oss-120b": {
        "display": "GPT-OSS 120B",
        "description": (
            "Reasoning model — MoE architecture, 117B total / 5.1B active parameters per token, "
            "128 experts. TTFT (time-to-first-token) here is observed response-start latency for "
            "this run only — it is not evidence of hidden reasoning depth or model quality."
        ),
        "architecture": "MoE",
        "total_params": "117B",
        "active_params": "5.1B",
        "task_fit": "reasoning",
    },
    "zai-glm-4.7": {
        "display": "GLM 4.7",
        "description": (
            "ZhipuAI's GLM-4 series — MoE architecture, 355B total / 32B active parameters per token. "
            "TTFT (time-to-first-token) here is observed response-start latency for this run only — "
            "it is not evidence of model quality."
        ),
        "architecture": "MoE",
        "total_params": "355B",
        "active_params": "32B",
        "task_fit": "interactive",
    },
}

# Canonical rubric — these keys are used BOTH for the human-readable rubric
# returned from /battle AND as the exact score keys the judge model must
# return, so display and grading can never silently diverge.
RUBRIC = {
    "relevance": "How well do the picks match the reader's stated taste dimensions and themes? (0-10)",
    "diversity": "How diverse are the picks across sub-genres, time periods, and authors? (0-10)",
    "reasoning_depth": "How specific and insightful is the reasoning for each pick, vs. generic boilerplate? (0-10)",
    "novelty": "How surprising/non-obvious are the picks vs. what the reader likely already knows? (0-10)",
    "specificity": "How tied is the reasoning to THIS reader's profile, rather than a generic reader? (0-10)",
}

RECOMMENDER_SYSTEM_PROMPT = guarded_system_prompt(
    "You are a book recommendation expert. Always respond with valid JSON only, no markdown."
)
OLLAMA_JUDGE_SYSTEM_PROMPT = guarded_system_prompt(
    "You are an expert evaluator. Always respond with valid JSON only."
)

MIN_ACCEPTABLE_RECS = 3
TARGET_RECS = 5
MODEL_ERROR_MAX_CHARS = 1000

# Average-score differences at or below this are treated as a tie rather
# than a "winner" — floating-point/model-noise-level differences (e.g. 7.4
# vs 7.42) shouldn't manufacture a decisive verdict.
JUDGE_SCORE_TIE_EPSILON = 0.05

BATTLE_SAMPLE_TARGET = 80
BATTLE_REVIEW_EXCERPT_CHARS = min(200, MAX_REVIEW_EXCERPT_CHARS)

_TITLE_ARTICLES_RE = re.compile(r"^(the|a|an)\s+")
_TITLE_WHITESPACE_RE = re.compile(r"\s+")

_ISBN_CLEAN_RE = re.compile(r"[\s\-]")
_ISBN_VALID_RE = re.compile(r"^(?:\d{9}[\dXx]|\d{13})$")

_client = None


def _bounded_model_error(error: BaseException | str) -> str:
    message = (
        safe_exception_summary(error)
        if isinstance(error, BaseException)
        else (error or "Model failed")
    )
    if len(message) <= MODEL_ERROR_MAX_CHARS:
        return message
    return message[: MODEL_ERROR_MAX_CHARS - 3] + "..."


def _get_client():
    """Lazily construct (and cache) the Cerebras client.

    Deferred import keeps this module importable — and therefore unit
    testable — without the cerebras SDK installed, and avoids constructing
    a network client at import time before an API key is configured.
    """
    global _client
    if _client is None:
        import os

        from cerebras.cloud.sdk import AsyncCerebras

        _client = AsyncCerebras(api_key=os.environ.get("CEREBRAS_API_KEY"))
    return _client


def _unicode_fold(text: str) -> str:
    """Casefold + diacritic-fold text (NFKD-decompose, then drop combining
    marks) so e.g. "café"/"Cafe" collide. Unicode-aware: this only removes
    combining accents, it does not touch base characters, so CJK and other
    non-Latin scripts pass through unaffected (they have no combining marks
    to strip)."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def _keep_alnum_or_space(text: str) -> str:
    """Keep any Unicode alphanumeric character (letters/digits from ANY
    script — Latin, CJK, Cyrillic, etc.) or whitespace, replacing everything
    else with a space. Deliberately Unicode-aware: the previous [a-z0-9]
    ASCII-only filter silently collapsed every non-Latin-script title to an
    empty string, making all CJK (or Greek, Cyrillic, Arabic, ...) titles
    indistinguishable from each other and from a missing title."""
    return "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in text)


def canonical_title(title: str) -> str:
    """Normalize a title for de-duplication / already-read comparisons —
    Unicode-aware casefold + diacritic-fold, strip a leading English article,
    keep only alphanumeric Unicode characters (any script), collapse
    whitespace."""
    t = (title or "").strip()
    if not t:
        return ""
    t = _unicode_fold(t)
    t = _TITLE_ARTICLES_RE.sub("", t)
    t = _keep_alnum_or_space(t)
    return _TITLE_WHITESPACE_RE.sub(" ", t).strip()


def _normalize_isbn(raw: str) -> str:
    """Strip whitespace/hyphens and uppercase a trailing ISBN-10 check digit."""
    return _ISBN_CLEAN_RE.sub("", raw or "").upper()


def _is_plausible_isbn(raw: str) -> bool:
    """Cheap shape check (ISBN-10 or ISBN-13) before spending a network
    round-trip verifying it — clearly-malformed strings (a year, an empty
    string, free text the model hallucinated) are rejected outright."""
    return bool(_ISBN_VALID_RE.match(raw))


def _open_library_candidate_matches(candidate: dict, title: str, author: str) -> bool:
    """Require exact canonical title evidence and, when the recommendation
    names an author, matching non-empty author evidence from Open Library."""
    if canonical_title(candidate.get("title", "")) != canonical_title(title):
        return False
    expected_author = _canonical_author(author)
    candidate_author = _canonical_author(candidate.get("author", ""))
    if expected_author and not candidate_author:
        return False
    return not expected_author or _authors_plausibly_match(candidate_author, expected_author)


def _canonical_author(author: str) -> str:
    """Same Unicode-aware normalization as canonical_title (casefold +
    diacritic-fold + alnum-only), without the leading-article strip (author
    names don't have articles to strip)."""
    a = (author or "").strip()
    if not a:
        return ""
    a = _unicode_fold(a)
    a = _keep_alnum_or_space(a)
    return _TITLE_WHITESPACE_RE.sub(" ", a).strip()


def _authors_plausibly_match(a: str, b: str) -> bool:
    """Lenient author comparison used only to avoid rejecting a correct ISBN
    over formatting differences ("J.R.R. Tolkien" vs "Tolkien, J. R. R."):
    true if either side is unknown (nothing to contradict), or the two
    canonical names share a meaningful (len >= 3) token such as a last name."""
    ca, cb = _canonical_author(a), _canonical_author(b)
    if not ca or not cb:
        return True
    tokens_a = {t for t in ca.split() if len(t) >= 3}
    tokens_b = {t for t in cb.split() if len(t) >= 3}
    return bool(tokens_a & tokens_b)


def build_exclude_index(*book_lists: list[dict]) -> dict[str, list[str]]:
    """Build a canonical-title -> [raw author strings] index from the
    reader's already-read/currently-reading/DNF/TBR books, used by
    validate_and_filter_recommendations to drop picks the reader already
    knows about.

    An empty-string entry means a shelf book with that title exists but its
    author is blank/unknown — that entry is a deliberate signal to fall back
    to conservative title-only exclusion for that title (see _is_excluded),
    since there isn't enough evidence to tell two same-titled books apart.
    """
    index: dict[str, list[str]] = {}
    for books in book_lists:
        for b in books:
            ct = canonical_title(b.get("title", ""))
            if not ct:
                continue
            index.setdefault(ct, []).append(b.get("author", "") or "")
    return index


def _is_excluded(title: str, author: str, exclude_index: dict[str, list[str]]) -> bool:
    """True if `title`/`author` should be dropped as already-read/current/
    DNF/TBR.

    Matches on canonical title + author when BOTH the shelf entry and the
    candidate recommendation have a known author (lenient comparison via
    _authors_plausibly_match, tolerant of formatting differences like
    "J.R.R. Tolkien" vs "Tolkien, J. R. R.") — this stops two genuinely
    distinct books that merely happen to share a title (e.g. two different
    novels both titled "Circe") from being conflated, which would otherwise
    either falsely exclude a legitimate new recommendation or mislabel it.

    Falls back to title-only exclusion — the prior, more conservative
    behavior — whenever either side's author is missing, since there isn't
    enough evidence in that case to safely tell the books apart.
    """
    shelf_authors = exclude_index.get(canonical_title(title))
    if shelf_authors is None:
        return False
    candidate_author_known = bool(_canonical_author(author))
    for shelf_author in shelf_authors:
        if not candidate_author_known or not _canonical_author(shelf_author):
            return True  # author evidence missing on one side -> conservative title-only match
        if _authors_plausibly_match(author, shelf_author):
            return True
    return False


async def _resolve_isbn_for_group(title: str, author: str, supplied_isbn: str) -> tuple[Optional[str], Optional[str]]:
    """Verify/resolve a single ISBN-lookup group (one canonical title +
    canonical author + supplied-ISBN combination, possibly shared by both
    models' picks).

    Returns (isbn_or_None, warning_or_None). A supplied ISBN that resolves to
    a matching title/author is kept as-is. A supplied ISBN that resolves to a
    different book, or doesn't resolve at all, is a confirmed mismatch/no-
    match (not an outage, so no warning is needed for that alone) — but that
    no longer means giving up: we fall through to a title+author search to
    try to find a *real* ISBN instead of leaving the pick unenriched. When no
    trustworthy ISBN was supplied at all, the same title+author search runs
    directly. A lookup outage (network error / malformed response) never
    fails the caller — it returns (None, a visible warning) so the pick's
    title/author/reason survive untouched with the ISBN simply omitted as
    unverified.
    """
    if supplied_isbn and _is_plausible_isbn(supplied_isbn):
        candidate, warning = await lookup_open_library(isbn=supplied_isbn, timeout=ISBN_VERIFY_TIMEOUT_SECONDS)
        if warning:
            return None, f"Could not verify the ISBN for {title!r} ({warning}); omitting unverified ISBN."
        if (
            candidate
            and _open_library_candidate_matches(candidate, title, author)
        ):
            return supplied_isbn, None
        # Confirmed mismatch (resolves to a different book) or no match at
        # all for this ISBN — don't trust a hallucinated/wrong-edition ISBN,
        # but don't stop here either; try to resolve a real one below.

    # No trustworthy ISBN was supplied, or the one supplied didn't verify —
    # try to resolve a real one via title+author search.
    candidate, warning = await lookup_open_library(title=title, author=author or None, timeout=ISBN_VERIFY_TIMEOUT_SECONDS)
    if warning:
        return None, f"Could not resolve an ISBN for {title!r} ({warning})."
    if candidate and _open_library_candidate_matches(candidate, title, author):
        resolved_isbn = _normalize_isbn(str(candidate.get("isbn") or ""))
        if _is_plausible_isbn(resolved_isbn):
            return resolved_isbn, None
    return None, None


async def enrich_recommendations_with_isbn(recs: list[dict]) -> list[str]:
    """Verify/enrich the ISBNs of the (at most TARGET_RECS per model)
    surviving recommendations in place, so a hallucinated or wrong-edition
    ISBN never reaches the frontend as if it were trustworthy.

    Lookups are deduplicated across recs that share the same canonical title
    + canonical author + supplied ISBN (e.g. both models independently
    recommending the same consensus pick), run under bounded concurrency
    (ISBN_VERIFY_CONCURRENCY) with a finite per-lookup timeout
    (ISBN_VERIFY_TIMEOUT_SECONDS), and never fail the battle: a lookup
    outage leaves title/author/reason untouched, drops the unverified ISBN,
    and is surfaced as a returned warning instead of raising. Mutates each
    rec's "isbn" field; returns the warnings list.
    """
    if not recs:
        return []

    # Grouped by canonical title + canonical author + supplied ISBN (not
    # just title + ISBN) so two genuinely distinct same-titled books by
    # different (known) authors are never conflated into one lookup/result —
    # the same title+author-aware principle used for already-read/TBR
    # matching elsewhere in this module.
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for rec in recs:
        key = (
            canonical_title(rec.get("title", "")),
            _canonical_author(rec.get("author", "")),
            _normalize_isbn(rec.get("isbn", "")),
        )
        groups.setdefault(key, []).append(rec)

    semaphore = asyncio.Semaphore(ISBN_VERIFY_CONCURRENCY)

    async def _resolve_one(key: tuple[str, str, str]):
        _canon_title, _canon_author, norm_isbn = key
        members = groups[key]
        title = members[0].get("title", "")
        author = members[0].get("author", "")
        async with semaphore:
            return await _resolve_isbn_for_group(title, author, norm_isbn)

    keys = list(groups)  # fixed, ordered snapshot — gather() preserves this order in `outcomes`
    outcomes = await asyncio.gather(*(_resolve_one(k) for k in keys), return_exceptions=True)

    warnings: list[str] = []
    for key, outcome in zip(keys, outcomes, strict=True):
        # Cancellation must never be mistaken for an ordinary lookup failure.
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome
        if isinstance(outcome, BaseException):
            summary = safe_exception_summary(outcome)
            logger.warning("ISBN verification group failed unexpectedly: %s", summary)
            warnings.append(
                f"ISBN verification failed unexpectedly ({summary}); "
                "leaving affected pick(s) unverified."
            )
            # Never leave the original (unverified, possibly hallucinated or
            # wrong-edition) LLM-supplied ISBN in place just because the
            # verification step itself raised — an unhandled failure here
            # must not silently look "trusted".
            for rec in groups[key]:
                rec["isbn"] = ""
            continue
        resolved_isbn, warning = outcome
        for rec in groups[key]:
            rec["isbn"] = resolved_isbn or ""
        if warning:
            warnings.append(warning)

    return warnings


def build_battle_prompt(
    dna: dict,
    books: list[dict],
    currently_reading: Optional[list[dict]] = None,
    dnf: Optional[list[dict]] = None,
    want_to_read: Optional[list[dict]] = None,
) -> str:
    currently_reading = currently_reading or []
    dnf = dnf or []
    want_to_read = want_to_read or []

    # A deterministic, representative sample across rating buckets and
    # recency — not the first/highest-rated N — so the model sees a
    # realistic picture of this reader's whole history, including books
    # they didn't love. Each line carries title, author, the reader's own
    # rating, the Goodreads average rating when known, and a bounded review
    # excerpt, matching the sampling used for the Reading DNA profile.
    sample = build_representative_sample(books, BATTLE_SAMPLE_TARGET)
    read_lines = "\n".join(format_book_line(b, BATTLE_REVIEW_EXCERPT_CHARS) for b in sample)

    dnf_titles = [sanitize_for_prompt(b.get("title", "")) for b in dnf]
    cr_titles = [sanitize_for_prompt(b.get("title", "")) for b in currently_reading]
    tbr_titles = [sanitize_for_prompt(b.get("title", "")) for b in want_to_read]

    dnf_note = f"\nBooks they started but did NOT finish (do NOT recommend these — something didn't click):\n{', '.join(dnf_titles)}" if dnf_titles else ""
    cr_note = f"\nCurrently reading (do NOT recommend these — they already have them):\n{', '.join(cr_titles)}" if cr_titles else ""
    # TBR picks are allowed (they aren't excluded downstream, unlike
    # read/currently-reading/DNF) — but shouldn't dominate the 5 picks, since
    # the reader already knows about them. Any pick that matches this list is
    # labeled on_tbr:true in the response so the reader can see that.
    tbr_note = (
        f"\nAlready on their want-to-read list — you MAY recommend one of these if it's a genuinely great fit, "
        f"but don't let your picks be dominated by their existing TBR list (they'll be labeled as already-on-TBR "
        f"in the response so the reader knows they already know about it):\n{', '.join(tbr_titles[:30])}"
        if tbr_titles else ""
    )

    # dna fields are LLM-generated (from a prior stage) but ultimately
    # derived from the same untrusted Goodreads text, so they are sanitized
    # here too as defense in depth before being re-embedded in this prompt.
    archetype = sanitize_for_prompt(str(dna.get("reader_archetype") or ""))
    taste_summary = sanitize_for_prompt(str(dna.get("taste_summary") or ""))
    top_themes = ", ".join(sanitize_for_prompt(str(t)) for t in dna.get("top_themes", []) or [])
    avoid_themes = ", ".join(sanitize_for_prompt(str(t)) for t in dna.get("avoid_themes", []) or [])
    raw_fiction_ratio = dna.get("taste_dimensions", {}).get("fiction_ratio")
    fiction_ratio = int(raw_fiction_ratio if raw_fiction_ratio is not None else 50)
    fiction_preference = (
        "this reader is primarily a non-fiction reader; strongly prefer non-fiction recommendations"
        if fiction_ratio < 40
        else "this reader is primarily a fiction reader; strongly prefer fiction recommendations"
        if fiction_ratio > 60
        else "this reader reads a mix of fiction and non-fiction"
    )

    return f"""You are recommending books to a specific reader. Here is their Reading DNA profile:

Archetype: {archetype}
Taste summary: {taste_summary}
Top themes they love: {top_themes}
Themes to avoid: {avoid_themes}
Prose density preference: {dna.get('taste_dimensions', {}).get('prose_density')}/10
Pacing preference: {dna.get('taste_dimensions', {}).get('pacing_preference')}/10
Intellectual depth: {dna.get('taste_dimensions', {}).get('intellectual_depth')}/10
Fiction ratio: {fiction_ratio}% — {fiction_preference}

Books they've already read — a representative sample across rating levels and recency, up to {BATTLE_SAMPLE_TARGET} of {len(books)} total (do NOT recommend any of these):
{read_lines}{dnf_note}{cr_note}{tbr_note}

Recommend exactly 5 books. For each, explain specifically WHY it matches this reader's DNA.
Return ONLY valid JSON, no markdown fences:
{{
  "recommendations": [
    {{
      "title": "...",
      "author": "...",
      "year": "...",
      "isbn": "...",
      "why": "2-3 sentences specifically tied to this reader's taste profile",
      "comfort_zone": true,
      "hidden_gem": false
    }}
  ]
}}

Set comfort_zone to false for any pick that intentionally pushes them outside their usual taste. Include at least 1 comfort_zone=false pick.
Set hidden_gem to true for picks that are underseen — not on major bestseller lists, published more than 3 years ago, from a smaller press, or generally less talked-about online. Include at least 1 hidden_gem pick."""


def validate_and_filter_recommendations(
    raw_recs: list[dict],
    exclude_index: dict[str, list[str]],
    tbr_index: Optional[dict[str, list[str]]] = None,
) -> tuple[list[dict], list[str]]:
    """Validate raw LLM recommendation JSON with Pydantic, canonicalize
    titles, deduplicate (by title+author when author is known, title-only
    fallback when it's missing on either side), and drop anything the
    reader has already read/is currently reading/DNF'd.

    `exclude_index` is a canonical-title -> [raw author strings] mapping
    (see build_exclude_index) of the reader's read/currently-reading/DNF
    shelves ONLY — want-to-read is intentionally NOT included here. A
    candidate is dropped when _is_excluded finds a match against it: with an
    author match too when both sides have a known author, or on title alone
    when author evidence is missing on either side (see _is_excluded's
    docstring for why).

    `tbr_index` (optional) is the analogous index built from ONLY the
    reader's want-to-read shelf. A candidate that matches it is NOT dropped
    — a TBR pick is a legitimate recommendation (the reader hasn't read it
    yet) — but is labeled `on_tbr: true` in the result so the reader can see
    they already knew about it, rather than either silently excluding it or
    silently presenting it as a "new to you" discovery.

    Returns (valid_recommendations, warnings). The result is capped at
    TARGET_RECS (never more, even if the model over-produced). If fewer than
    TARGET_RECS picks survive validation/filtering, the valid remainder is
    returned along with an explicit warning — we never invent picks to hit
    the target count.
    """
    tbr_index = tbr_index or {}
    warnings: list[str] = []
    validated: list[dict] = []
    # Tracks canonical-title -> [raw author strings] for recs ALREADY kept
    # in this batch, reusing _is_excluded's title+author-aware matching to
    # decide whether a later candidate is a duplicate of an earlier one —
    # the same "title-only fallback only when author evidence is missing"
    # rule used for the reader's shelf, so two distinct same-titled books by
    # different known authors can both survive instead of the second being
    # wrongly treated as a repeat of the first.
    seen_index: dict[str, list[str]] = {}

    for raw in raw_recs:
        if not isinstance(raw, dict):
            warnings.append("Dropped a non-object recommendation entry from the model output.")
            continue
        try:
            item = RecommendationItem.model_validate(raw)
        except ValidationError as e:
            warnings.append(
                f"Dropped an invalid recommendation ({validation_error_summary(e)})."
            )
            continue

        canonical = canonical_title(item.title)
        if not canonical:
            continue
        if _is_excluded(item.title, item.author, seen_index):
            continue  # duplicate of an earlier survivor in this same batch
        if _is_excluded(item.title, item.author, exclude_index):
            continue  # already read / currently reading / DNF'd
        seen_index.setdefault(canonical, []).append(item.author)

        out = item.model_dump()
        out["on_tbr"] = _is_excluded(item.title, item.author, tbr_index)
        validated.append(out)

    if len(validated) > TARGET_RECS:
        validated = validated[:TARGET_RECS]

    if len(validated) < TARGET_RECS:
        warnings.append(
            f"Only {len(validated)} valid recommendation(s) remained after de-duplication and filtering "
            f"already-read/current/DNF titles (target was {TARGET_RECS})."
        )

    return validated, warnings


async def call_model(model: str, prompt: str, retries: int = 3, system_prompt: str = "") -> dict:
    """`system_prompt` defaults to the recommender's guarded system prompt
    (RECOMMENDER_SYSTEM_PROMPT) when not supplied, so existing recommender
    call sites are unaffected; callers with a different task (e.g.
    prediction) can pass their own dedicated, guarded system prompt instead
    of implicitly inheriting the recommender's."""
    for attempt in range(retries):
        try:
            return await _call_model_once(model, prompt, system_prompt=system_prompt)
        except asyncio.CancelledError:
            raise  # never swallow cancellation
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt  # 1s, 2s, 4s
            logger.warning(
                "%s attempt %d failed (%s), retrying in %ds...",
                model,
                attempt + 1,
                safe_exception_summary(e),
                wait,
            )
            await asyncio.sleep(wait)
    raise RuntimeError("unreachable")


async def _stream_completion(model: str, prompt: str, state: dict, system_prompt: str = "") -> None:
    """Consume the streaming completion, recording TTFT on the first
    non-empty content delta (perf_counter, not first stream event)."""
    client = _get_client()
    stream = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt or RECOMMENDER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        stream=True,
    )
    async for chunk in stream:
        if chunk.choices:
            delta = chunk.choices[0].delta.content
            if delta:
                if state["ttft"] is None:
                    state["ttft"] = time.perf_counter()
                state["chunks"].append(delta)
            if chunk.choices[0].finish_reason:
                state["finish_reason"] = chunk.choices[0].finish_reason
        if chunk.usage:
            state["prompt_tokens"] = chunk.usage.prompt_tokens
            state["completion_tokens"] = chunk.usage.completion_tokens


async def _call_model_once(model: str, prompt: str, system_prompt: str = "") -> dict:
    t0 = time.perf_counter()
    state: dict = {
        "ttft": None,
        "chunks": [],
        "finish_reason": None,
        "prompt_tokens": None,
        "completion_tokens": None,
    }

    try:
        await call_with_limit(_stream_completion(model, prompt, state, system_prompt=system_prompt), timeout=LLM_ATTEMPT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise TimeoutError(f"{model} timed out after {LLM_ATTEMPT_TIMEOUT_SECONDS}s") from None

    t_end = time.perf_counter()
    ttft = state["ttft"]
    ttft_ms = round((ttft - t0) * 1000) if ttft is not None else None
    generation_ms = round((t_end - ttft) * 1000) if ttft is not None else None
    total_ms = round((t_end - t0) * 1000)

    text = "".join(state["chunks"]).strip()
    if not text:
        logger.warning(
            "%s returned an empty response (finish_reason=%s, chunks=%d)",
            model, state["finish_reason"], len(state["chunks"]),
        )
        raise ValueError(f"{model} returned an empty response (finish_reason={state['finish_reason']})")
    # Strip markdown fences
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    # If still not valid JSON, extract the outermost {...} block
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                data = json.loads(text[start:end])
            except json.JSONDecodeError as e:
                # Never log the raw model response — only metadata (model
                # name, error, and response length) to avoid leaking
                # untrusted/PII-adjacent user or model content into logs.
                logger.error(
                    "%s response JSON parse error: %s (response length=%d)",
                    model,
                    safe_exception_summary(e),
                    len(text),
                )
                raise
        else:
            logger.error("%s response contained no JSON object (response length=%d)", model, len(text))
            raise ValueError(f"No JSON object found in {model} response")
    data["_meta"] = {
        "latency_ms": total_ms,
        "ttft_ms": ttft_ms,
        "generation_ms": generation_ms,
        "prompt_tokens": state["prompt_tokens"],
        "completion_tokens": state["completion_tokens"],
    }
    return data


def build_judge_prompt(dna: dict, recs: list[dict], recommender_label: str) -> str:
    """`recommender_label` is an anonymized label (e.g. "Recommender A"),
    never the real model name — this blinds the judge to model identity.

    Recommendation titles/authors/reasoning are themselves LLM output (from
    the recommending model), which can echo untrusted Goodreads text back
    verbatim — they are sanitized here too, same as any other untrusted
    text embedded in a prompt.
    """
    rec_text = "\n".join(
        f"{i+1}. \"{sanitize_for_prompt(r.get('title', ''))}\" by {sanitize_for_prompt(r.get('author', ''))}\n"
        f"   Reasoning: {sanitize_for_prompt(r.get('why', '')) or 'No reasoning provided'}"
        for i, r in enumerate(recs)
    )
    dims = "\n".join(f'    "{k}": <0-10, {v}>' for k, v in RUBRIC.items())
    archetype = sanitize_for_prompt(str(dna.get("reader_archetype") or ""))
    taste_summary = sanitize_for_prompt(str(dna.get("taste_summary") or ""))
    top_themes = ", ".join(sanitize_for_prompt(str(t)) for t in dna.get("top_themes", []) or [])
    avoid_themes = ", ".join(sanitize_for_prompt(str(t)) for t in dna.get("avoid_themes", []) or [])
    return f"""You are an expert literary critic and AI evaluation researcher judging book recommendations.

The reader's profile:
- Archetype: {archetype}
- Taste summary: {taste_summary}
- Top themes: {top_themes}
- Themes to avoid: {avoid_themes}
- Prose density: {dna.get('taste_dimensions', {}).get('prose_density')}/10
- Pacing: {dna.get('taste_dimensions', {}).get('pacing_preference')}/10
- Intellectual depth: {dna.get('taste_dimensions', {}).get('intellectual_depth')}/10

{recommender_label} recommended these books:
{rec_text}

Score {recommender_label} on each dimension from 0-10, then write a 2-sentence verdict.

Return ONLY valid JSON:
{{
  "scores": {{
{dims}
  }},
  "verdict": "<2 sentences on the overall quality of these recommendations and reasoning>"
}}"""


async def call_ollama_judge(prompt: str, model: str = "qwen2.5:7b", timeout: float = LLM_ATTEMPT_TIMEOUT_SECONDS * 4) -> dict:
    """Local Ollama judge call. Given a much larger local-CPU latency budget
    than the paid Cerebras calls, but still bounded — a hung local server
    should not block a request forever. Also bounded by the shared
    MAX_LLM_CONCURRENCY semaphore, so a burst of judge calls can't fan out
    unbounded local-CPU work alongside the paid Cerebras calls."""
    t0 = time.perf_counter()

    async def _post() -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": OLLAMA_JUDGE_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "format": "json",
                },
            )
            resp.raise_for_status()
            return resp.json()

    try:
        payload = await call_with_limit(_post(), timeout=timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(f"Ollama judge call ({model}) timed out after {timeout}s") from None
    latency_ms = round((time.perf_counter() - t0) * 1000)
    text = payload["message"]["content"].strip()
    data = json.loads(text)
    data["_judge_latency_ms"] = latency_ms
    data["_judge_model"] = model
    return data


async def run_battle(
    dna: dict,
    books: list[dict],
    currently_reading: Optional[list[dict]] = None,
    dnf: Optional[list[dict]] = None,
    want_to_read: Optional[list[dict]] = None,
) -> dict:
    currently_reading = currently_reading or []
    dnf = dnf or []
    want_to_read = want_to_read or []

    prompt = build_battle_prompt(dna, books, currently_reading, dnf, want_to_read)

    # Read/currently-reading/DNF are hard exclusions (the reader already
    # knows/has/rejected these). Want-to-read is tracked SEPARATELY: a TBR
    # match is a legitimate recommendation (they haven't read it), so it is
    # labeled on_tbr=true rather than dropped — see validate_and_filter_recommendations.
    exclude_index = build_exclude_index(books, currently_reading, dnf)
    tbr_index = build_exclude_index(want_to_read)

    gpt_recs, glm_recs = await asyncio.gather(
        call_model("gpt-oss-120b", prompt),
        call_model("zai-glm-4.7", prompt),
        return_exceptions=True,
    )

    models = {
        "GPT-OSS 120B": gpt_recs,
        "GLM 4.7": glm_recs,
    }

    results = {}
    battle_warnings: list[str] = []
    for model_id, (name, recs) in zip(
        ["gpt-oss-120b", "zai-glm-4.7"], models.items(), strict=True
    ):
        info = MODEL_INFO.get(model_id, {})
        # Cancellation must never be mistaken for an ordinary model error —
        # re-raise it so the caller (and asyncio) see the request was
        # actually cancelled, not that the model "failed".
        if isinstance(recs, asyncio.CancelledError):
            raise recs
        if isinstance(recs, BaseException):
            results[name] = {
                "error": _bounded_model_error(recs),
                "recommendations": [],
                "meta": None,
                "info": info,
            }
            continue
        if not isinstance(recs, dict):
            results[name] = {
                "error": _bounded_model_error("Model response was not a JSON object."),
                "recommendations": [],
                "meta": None,
                "info": info,
            }
            continue

        meta = recs.pop("_meta", {})
        raw_recs = recs.get("recommendations", [])
        if not isinstance(raw_recs, list):
            results[name] = {
                "error": _bounded_model_error("Model returned a non-list recommendations field."),
                "recommendations": [],
                "meta": meta if isinstance(meta, dict) else None,
                "info": info,
            }
            continue
        validated, rec_warnings = validate_and_filter_recommendations(raw_recs, exclude_index, tbr_index)
        results[name] = {
            "recommendations": validated,
            "meta": meta,
            "info": info,
        }
        if rec_warnings:
            results[name]["warnings"] = rec_warnings
            battle_warnings.extend(f"{name}: {w}" for w in rec_warnings)

    # LLM-supplied ISBNs are never trusted blindly: verify/enrich the at
    # most TARGET_RECS-per-model surviving picks against Open Library before
    # returning them, deduplicating lookups across both models' picks (e.g.
    # a consensus pick recommended by both). This mutates each rec's "isbn"
    # field in place; any lookup outage surfaces as a warning rather than
    # failing the battle.
    all_recs = [rec for r in results.values() for rec in r.get("recommendations", [])]
    isbn_warnings = await enrich_recommendations_with_isbn(all_recs)
    battle_warnings.extend(isbn_warnings)

    return {"models": results, "rubric": RUBRIC, "warnings": battle_warnings}


async def run_judge(dna: dict, battle_results: dict) -> dict:
    """Independently score each anonymized model's recommendations using a
    local judge model (NOT pairwise/order-swapped cross-evaluation — each
    recommender's list is judged on its own merits by a separate judge call;
    there is no swapping of the two models' positions to control for order
    bias, only randomized A/B label *assignment* per run so the judge never
    sees which physical model produced which list).

    Model identity is blinded from the judge (anonymized as "Recommender A"/
    "Recommender B" in a randomized mapping). Each judge call's raw JSON is
    validated with JudgeVerdictPayload (rejecting missing OR extra rubric
    score keys) before being trusted. A single judge call failing
    (exception, timeout, or invalid JSON) is surfaced as
    result['judge'][model_display] = {"error": ...} — the other model's
    successful judge result is preserved, not discarded. Only when BOTH
    judge calls fail does this raise, so the caller never returns a
    success-shaped payload with a fabricated winner. If only ONE judge call
    succeeds, `winner` is forced to None — a lone scored model is never
    declared the winner against an unscored competitor.
    """
    models_data = battle_results.get("models", {})
    if not isinstance(models_data, dict):
        raise ValueError("battle_results.models must be an object")
    model_names = [n for n in ("GPT-OSS 120B", "GLM 4.7") if n in models_data]
    if len(model_names) < 2:
        raise ValueError("run_judge requires recommendations from both models in battle_results")

    judge_results: dict = {}
    errors: dict = {}
    eligible_names: list[str] = []
    for model_name in model_names:
        model_result = models_data[model_name]
        if not isinstance(model_result, dict):
            message = "Recommender result was not an object."
        elif model_result.get("error"):
            message = f"Recommender failed before judging: {model_result['error']}"
        elif not isinstance(model_result.get("recommendations"), list):
            message = "Recommender recommendations were not a list."
        elif not model_result["recommendations"]:
            message = "Recommender returned no recommendations to judge."
        elif any(not isinstance(rec, dict) for rec in model_result["recommendations"]):
            message = "Recommender returned a malformed recommendation."
        else:
            eligible_names.append(model_name)
            continue
        errors[model_name] = message
        judge_results[model_name] = {"error": message}

    if not eligible_names:
        raise ValueError("No successful, non-empty recommender result is eligible for judging.")

    labels = [f"Recommender {chr(ord('A') + index)}" for index in range(len(eligible_names))]
    order = list(eligible_names)
    random.shuffle(order)  # randomize which model gets which anonymized label, so the judge can't infer identity from label order
    label_for_model = dict(zip(order, labels, strict=True))
    model_for_label = {label: name for name, label in label_for_model.items()}

    recs_by_label = {
        label_for_model[name]: models_data.get(name, {}).get("recommendations", [])
        for name in order
    }

    verdicts = await asyncio.gather(
        *(call_ollama_judge(build_judge_prompt(dna, recs_by_label[label], label)) for label in labels),
        return_exceptions=True,
    )

    for label, verdict in zip(labels, verdicts, strict=True):
        model_name = model_for_label[label]

        # Cancellation must never be mistaken for an ordinary judge error —
        # re-raise it so the caller (and asyncio) see the request was
        # actually cancelled, not that the judge "failed".
        if isinstance(verdict, asyncio.CancelledError):
            raise verdict
        if isinstance(verdict, BaseException):
            message = safe_exception_summary(verdict)
            errors[model_name] = message
            judge_results[model_name] = {"error": message}
            continue

        latency = verdict.pop("_judge_latency_ms", None)
        judge_model = verdict.pop("_judge_model", None)
        try:
            payload = JudgeVerdictPayload.model_validate(verdict)
            missing_dims = set(RUBRIC) - set(payload.scores)
            extra_dims = set(payload.scores) - set(RUBRIC)
            if missing_dims or extra_dims:
                problems = []
                if missing_dims:
                    problems.append(f"missing {sorted(missing_dims)}")
                if extra_dims:
                    problems.append(f"unexpected {sorted(extra_dims)}")
                raise ValueError(f"rubric dimension mismatch: {'; '.join(problems)}")
        except (ValidationError, ValueError) as e:
            summary = (
                validation_error_summary(e)
                if isinstance(e, ValidationError)
                else safe_exception_summary(e)
            )
            message = f"Judge response failed validation: {summary}"
            logger.warning("Judge verdict for %s failed validation: %s", model_name, summary)
            errors[model_name] = message
            judge_results[model_name] = {"error": message}
            continue

        judge_results[model_name] = {
            "scores": payload.scores,
            "verdict": payload.verdict,
            "latency_ms": latency,
            "model": judge_model,
        }

    successful = {name: r for name, r in judge_results.items() if "error" not in r}
    if not successful:
        # Both judge calls failed — this must surface as an explicit error, not a
        # success-shaped payload with a fabricated winner.
        raise RuntimeError(f"Judge evaluation failed for all recommenders: {errors}")

    def avg_score(name: str) -> Optional[float]:
        # Only RUBRIC keys ever reach here (extra keys are rejected above),
        # so this average can't be skewed by an unexpected dimension.
        scores = successful.get(name, {}).get("scores", {})
        return sum(scores.values()) / len(scores) if scores else None

    scored_names = [n for n in model_names if n in successful and avg_score(n) is not None]

    winner: Optional[str] = None
    tie = False
    if len(scored_names) >= 2:
        best_score = max(avg_score(n) for n in scored_names)
        # Scores within JUDGE_SCORE_TIE_EPSILON of the best are treated as
        # tied — a 0.02-point difference from model-to-model noise shouldn't
        # manufacture a decisive "winner".
        tied = [n for n in scored_names if best_score - avg_score(n) <= JUDGE_SCORE_TIE_EPSILON]
        winner = tied[0] if len(tied) == 1 else None
        tie = len(tied) > 1
    elif len(scored_names) == 1:
        # Only one of the two models actually got scored — never crown it
        # winner against a competitor that has no score to compare against.
        winner = None
        tie = False
    else:
        raise RuntimeError(f"Judge returned no usable scores for either recommender: {errors}")

    if winner is not None and winner not in model_names:
        raise RuntimeError("Judge selected an unknown recommender.")

    result: dict = {
        "judge": judge_results,
        "winner": winner,
        "tie": tie,
    }
    if errors:
        result["errors"] = errors
    return result
