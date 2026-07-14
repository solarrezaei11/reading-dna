"""Lightweight in-memory rate limiting and concurrency guards.

No external dependencies (e.g. redis) — this is a single-process API, so a
plain dict + sliding window counter and asyncio.Semaphore are sufficient and
keep the dependency footprint unchanged.
"""
import asyncio
import time
from collections import defaultdict, deque
from ipaddress import ip_address

from fastapi import HTTPException, Request

from config import (
    RATE_LIMIT_CLEANUP_INTERVAL_SECONDS,
    RATE_LIMIT_MAX_REQUESTS,
    RATE_LIMIT_TRUSTED_PROXY_HOPS,
    RATE_LIMIT_WINDOW_SECONDS,
)


class SlidingWindowRateLimiter:
    """Per-key sliding-window rate limiter.

    Keyed by (client identifier, route name) so one expensive endpoint being
    hammered doesn't starve the rate budget of another.

    `_hits` never shrinks on its own as part of a normal `check()` call
    beyond the currently-accessed key's own deque, so a flood of distinct or
    spoofed client identifiers could otherwise grow this dict for the life
    of the process even long after every one of those keys' windows expired.
    To bound that without paying an O(n) cost on every request, a full
    sweep of stale keys runs at most once every `cleanup_interval_seconds`
    (wall-clock), piggy-backed onto whichever request happens to be the
    first to observe the interval has elapsed.
    """

    def __init__(
        self,
        max_requests: int = RATE_LIMIT_MAX_REQUESTS,
        window_seconds: float = RATE_LIMIT_WINDOW_SECONDS,
        cleanup_interval_seconds: float = RATE_LIMIT_CLEANUP_INTERVAL_SECONDS,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._last_cleanup = time.monotonic()

    async def check(self, key: str) -> None:
        """Raise HTTPException(429) if `key` has exceeded its budget; else record the hit."""
        now = time.monotonic()
        async with self._lock:
            # Run before touching `key` specifically: this way the periodic
            # sweep isn't starved by a client that is itself continuously
            # over budget (and would otherwise always hit the early `raise`
            # below before ever reaching cleanup).
            self._cleanup_stale_keys_if_due(now)

            hits = self._hits[key]
            cutoff = now - self.window_seconds
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self.max_requests:
                retry_after = max(0.0, hits[0] + self.window_seconds - now)
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded ({self.max_requests} requests / {int(self.window_seconds)}s). "
                    f"Retry in {retry_after:.0f}s.",
                )
            hits.append(now)

    def _cleanup_stale_keys_if_due(self, now: float) -> None:
        """Drop keys whose entire sliding window has expired.

        Must be called while holding `self._lock`. Only actually sweeps
        every `cleanup_interval_seconds`, so this is periodic maintenance,
        not an O(n) cost on every request. A key's deque is append-only in
        time order, so checking just its newest entry (`hits[-1]`) is
        enough to know the whole deque is stale — no need to inspect every
        element of every other key's deque.
        """
        if now - self._last_cleanup < self.cleanup_interval_seconds:
            return
        self._last_cleanup = now
        cutoff = now - self.window_seconds
        stale_keys = [k for k, hits in self._hits.items() if not hits or hits[-1] < cutoff]
        for k in stale_keys:
            del self._hits[k]


def client_key(
    request: Request,
    trusted_proxy_hops: int = RATE_LIMIT_TRUSTED_PROXY_HOPS,
) -> str:
    """Return a rate-limit key without trusting caller-controlled headers.

    Forwarded addresses are considered only when a deployment explicitly
    configures a fixed number of trusted proxy hops. Selecting from the
    right side prevents untrusted prefixes from changing the chosen client.
    Direct access must be blocked whenever this option is enabled.
    """
    peer = request.client.host.strip() if request.client and request.client.host else ""
    if trusted_proxy_hops > 0:
        forwarded = [
            value.strip()
            for value in request.headers.get("x-forwarded-for", "").split(",")
            if value.strip()
        ]
        if len(forwarded) >= trusted_proxy_hops:
            candidate = forwarded[-trusted_proxy_hops]
            try:
                return str(ip_address(candidate))
            except ValueError:
                pass
    return peer or "unknown"


# Shared limiter instances for expensive endpoints. Each endpoint gets its own
# budget so, e.g., a burst of /libby calls doesn't block /predict.
rss_limiter = SlidingWindowRateLimiter()
dna_limiter = SlidingWindowRateLimiter()
battle_limiter = SlidingWindowRateLimiter()
judge_limiter = SlidingWindowRateLimiter()
predict_limiter = SlidingWindowRateLimiter()
libby_limiter = SlidingWindowRateLimiter()
embeddings_limiter = SlidingWindowRateLimiter()
csv_limiter = SlidingWindowRateLimiter()
