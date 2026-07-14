"""Central, env-driven configuration for the backend.

All tunables live here so behavior can be adjusted per-deployment without
code changes. Every value has a safe default so the app runs out of the box
in local dev.

Fail-fast policy: an *absent* env var silently uses the documented default
(so the app still runs out of the box). An env var that IS SET but malformed
(not an int/float/bool, or out of its required bound) raises ValueError at
import time — i.e. at process startup — rather than silently falling back to
a default and running with a configuration nobody asked for. This is a
deliberate tradeoff: a misconfigured deployment should fail loudly and
immediately, not serve traffic with a silently-wrong tunable.
"""
import math
import os


class ConfigError(ValueError):
    """Raised when an environment variable is set but malformed, or a
    resolved config value violates a required bound. Always raised at
    import time so misconfiguration fails fast at startup."""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        raise ConfigError(f"Environment variable {name}={raw!r} is not a valid integer.") from None


def _env_int_with_alias(name: str, alias: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is not None and raw.strip():
        return _env_int(name, default)
    return _env_int(alias, default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        raise ConfigError(f"Environment variable {name}={raw!r} is not a valid float.") from None
    if not math.isfinite(value):
        raise ConfigError(f"Environment variable {name}={raw!r} must be finite.")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"Environment variable {name}={raw!r} is not a valid boolean (use true/false/1/0/yes/no/on/off).")


def _require_positive_int(name: str, value: int) -> int:
    if value <= 0:
        raise ConfigError(f"{name} must be a positive integer, got {value}.")
    return value


def _require_nonnegative_int(name: str, value: int) -> int:
    if value < 0:
        raise ConfigError(f"{name} must be a non-negative integer, got {value}.")
    return value


def _require_positive_float(name: str, value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{name} must be a positive number, got {value}.")
    return value


# --- CORS -------------------------------------------------------------
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

# --- Secrets / readiness -----------------------------------------------
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")

# --- Optional backend access token ---------------------------------------
# Unset (default) preserves today's open behavior. When set, API routes
# require `Authorization: Bearer <token>` (OPTIONS and /health are always
# exempt). This is a backend deployment control, not a public frontend
# secret — never wire this into any client-visible config.
BACKEND_ACCESS_TOKEN = (os.environ.get("BACKEND_ACCESS_TOKEN", "") or "").strip() or None

# --- Prediction logging (opt-in; avoids a silent global multi-user log) -
ENABLE_PREDICTION_LOG = _env_bool("ENABLE_PREDICTION_LOG", False)

# --- Request size guards -----------------------------------------------
MAX_JSON_BODY_BYTES = _require_positive_int("MAX_JSON_BODY_BYTES", _env_int("MAX_JSON_BODY_BYTES", 2 * 1024 * 1024))  # 2 MB
MAX_UPLOAD_BYTES = _require_positive_int("MAX_UPLOAD_BYTES", _env_int("MAX_UPLOAD_BYTES", 15 * 1024 * 1024))  # 15 MB CSV export
MAX_COLLECTION_SIZE = _require_positive_int("MAX_COLLECTION_SIZE", _env_int("MAX_COLLECTION_SIZE", 5000))  # max books per request list
MAX_REVIEW_EXCERPT_CHARS = _require_positive_int("MAX_REVIEW_EXCERPT_CHARS", _env_int("MAX_REVIEW_EXCERPT_CHARS", 400))

# --- Goodreads RSS pagination -------------------------------------------
GOODREADS_RSS_PER_PAGE = 200
GOODREADS_RSS_MAX_PAGES = _require_positive_int("GOODREADS_RSS_MAX_PAGES", _env_int("GOODREADS_RSS_MAX_PAGES", 25))  # 25 * 200 = 5000/shelf
GOODREADS_RSS_PAGE_DELAY_SECONDS = _env_float("GOODREADS_RSS_PAGE_DELAY_SECONDS", 0.4)
if GOODREADS_RSS_PAGE_DELAY_SECONDS < 0:
    raise ConfigError(f"GOODREADS_RSS_PAGE_DELAY_SECONDS must be >= 0, got {GOODREADS_RSS_PAGE_DELAY_SECONDS}.")

# --- Libby / OverDrive catalog -------------------------------------------
LIBBY_CATALOG_PER_PAGE = 100
# Live API check: /v2/libraries reports totalItems ~= 12,977, i.e. ~130 pages
# at perPage=100. Default gives headroom above that so a name-based lookup
# (the only path that needs the full catalog — direct URL/key lookups use a
# single-library GET instead) doesn't truncate the real directory.
LIBBY_CATALOG_MAX_PAGES = _require_positive_int("LIBBY_CATALOG_MAX_PAGES", _env_int("LIBBY_CATALOG_MAX_PAGES", 150))  # 150 * 100 = 15,000 libraries
LIBBY_CATALOG_TTL_SECONDS = _require_positive_int("LIBBY_CATALOG_TTL_SECONDS", _env_int("LIBBY_CATALOG_TTL_SECONDS", 6 * 3600))
LIBBY_MEDIA_CONCURRENCY = _require_positive_int("LIBBY_MEDIA_CONCURRENCY", _env_int("LIBBY_MEDIA_CONCURRENCY", 8))
# Bounds how many catalog *pages* (after page 1) are fetched concurrently
# when a free-text library-name lookup needs the full ~130-page directory.
# Sequential fetching of ~130 pages can exceed the frontend's request
# timeout; fetching concurrently (still deterministically re-ordered by
# page number) keeps latency bounded without changing the resulting data.
LIBBY_CATALOG_FETCH_CONCURRENCY = _require_positive_int(
    "LIBBY_CATALOG_FETCH_CONCURRENCY", _env_int("LIBBY_CATALOG_FETCH_CONCURRENCY", 10)
)

# --- Concurrency guards for paid LLM calls / CPU-bound analysis ---------
# MAX_LLM_CONCURRENCY bounds every outbound LLM call across the app (DNA
# generation, battle recommendations, both judge calls, prediction calls,
# and map/embeddings cluster naming) via a single shared semaphore in
# llm_client.py. LLM_CONCURRENCY is accepted as a legacy alias.
MAX_LLM_CONCURRENCY = _require_positive_int(
    "MAX_LLM_CONCURRENCY", _env_int_with_alias("MAX_LLM_CONCURRENCY", "LLM_CONCURRENCY", 4)
)
CPU_ANALYSIS_CONCURRENCY = _require_positive_int("CPU_ANALYSIS_CONCURRENCY", _env_int("CPU_ANALYSIS_CONCURRENCY", 2))
LLM_ATTEMPT_TIMEOUT_SECONDS = _require_positive_float(
    "LLM_ATTEMPT_TIMEOUT_SECONDS", _env_float("LLM_ATTEMPT_TIMEOUT_SECONDS", 45.0)
)

# --- Lightweight in-memory rate limiting ---------------------------------
RATE_LIMIT_WINDOW_SECONDS = _require_positive_float(
    "RATE_LIMIT_WINDOW_SECONDS", _env_float("RATE_LIMIT_WINDOW_SECONDS", 60.0)
)
RATE_LIMIT_MAX_REQUESTS = _require_positive_int("RATE_LIMIT_MAX_REQUESTS", _env_int("RATE_LIMIT_MAX_REQUESTS", 20))
# Zero (default) ignores forwarded headers. Set this only when direct access
# to the backend is restricted to a trusted proxy chain that appends or
# replaces X-Forwarded-For at every hop.
RATE_LIMIT_TRUSTED_PROXY_HOPS = _require_nonnegative_int(
    "RATE_LIMIT_TRUSTED_PROXY_HOPS", _env_int("RATE_LIMIT_TRUSTED_PROXY_HOPS", 0)
)
# How often (wall-clock seconds) each limiter sweeps its per-key dict for
# fully-expired keys (client/route combinations with no hits inside the
# current window). This is a *periodic* sweep, not a per-request one — a
# flood of distinct/spoofed client keys would otherwise grow `_hits`
# unboundedly for the life of the process even after every window expires.
RATE_LIMIT_CLEANUP_INTERVAL_SECONDS = _require_positive_float(
    "RATE_LIMIT_CLEANUP_INTERVAL_SECONDS", _env_float("RATE_LIMIT_CLEANUP_INTERVAL_SECONDS", 300.0)
)

# --- Embedding cache ------------------------------------------------------
EMBEDDING_CACHE_MAX_ENTRIES = _require_positive_int(
    "EMBEDDING_CACHE_MAX_ENTRIES", _env_int("EMBEDDING_CACHE_MAX_ENTRIES", 1000)
)

# --- Recommendation ISBN verification/enrichment -------------------------
# LLM-supplied ISBNs are never trusted blindly: at most TARGET_RECS * 2
# recommendations (one battle, two models) get a lightweight Open Library
# lookup to verify a supplied ISBN actually matches the recommended title,
# or to resolve a real ISBN when none/an untrustworthy one was supplied.
# Bounded concurrency + a finite per-lookup timeout keep this from turning
# an outage into an unbounded delay or a battle-ending failure.
ISBN_VERIFY_CONCURRENCY = _require_positive_int("ISBN_VERIFY_CONCURRENCY", _env_int("ISBN_VERIFY_CONCURRENCY", 5))
ISBN_VERIFY_TIMEOUT_SECONDS = _require_positive_float(
    "ISBN_VERIFY_TIMEOUT_SECONDS", _env_float("ISBN_VERIFY_TIMEOUT_SECONDS", 8.0)
)
