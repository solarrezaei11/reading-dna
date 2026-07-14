"""Tests for the in-memory sliding-window rate limiter."""
import unittest

from fastapi import HTTPException
from starlette.requests import Request

from rate_limit import SlidingWindowRateLimiter, client_key


class SlidingWindowRateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_allows_requests_under_the_limit(self):
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            await limiter.check("client-a")  # should not raise

    async def test_blocks_requests_over_the_limit(self):
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
        await limiter.check("client-a")
        await limiter.check("client-a")
        with self.assertRaises(HTTPException) as ctx:
            await limiter.check("client-a")
        self.assertEqual(ctx.exception.status_code, 429)

    async def test_keys_are_independent(self):
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
        await limiter.check("client-a")
        await limiter.check("client-b")  # different key, independent budget

    async def test_old_hits_expire_out_of_the_window(self):
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=0.05)
        await limiter.check("client-a")
        import asyncio
        await asyncio.sleep(0.1)
        await limiter.check("client-a")  # window has slid, should not raise

    async def test_stale_inactive_keys_are_removed_by_periodic_cleanup(self):
        # A short window and a short cleanup interval so the periodic sweep
        # is exercised deterministically within a fast unit test, without
        # mocking time.monotonic.
        limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=0.05, cleanup_interval_seconds=0.05)
        import asyncio

        await limiter.check("client-a")
        await limiter.check("client-b")
        self.assertIn("client-a", limiter._hits)
        self.assertIn("client-b", limiter._hits)

        # Let both keys' windows *and* the cleanup interval fully elapse,
        # then make an unrelated request from a third key — this is the
        # request that should observe the interval has passed and sweep
        # every key whose entire window has expired.
        await asyncio.sleep(0.15)
        await limiter.check("client-c")

        self.assertNotIn("client-a", limiter._hits)
        self.assertNotIn("client-b", limiter._hits)
        # The just-recorded key must survive its own sweep pass.
        self.assertIn("client-c", limiter._hits)

    async def test_cleanup_does_not_run_more_often_than_the_configured_interval(self):
        # A long cleanup interval relative to the window means many requests
        # after a key goes stale should NOT immediately drop it — proving
        # the sweep is periodic, not an O(n) check on every call.
        limiter = SlidingWindowRateLimiter(max_requests=20, window_seconds=0.01, cleanup_interval_seconds=60.0)
        import asyncio

        await limiter.check("client-a")
        await asyncio.sleep(0.05)  # client-a's window has now expired
        for _ in range(10):
            await limiter.check("client-b")  # unrelated traffic on other keys, well under its own budget

        # Still present: the 60s cleanup interval has not elapsed yet, even
        # though client-a's own sliding window has.
        self.assertIn("client-a", limiter._hits)

    async def test_active_key_survives_its_own_cleanup_pass(self):
        # A key with hits still inside its window must never be swept away
        # by the periodic cleanup, even when the cleanup interval elapses
        # in between two of its own requests.
        limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=5.0, cleanup_interval_seconds=0.01)
        import asyncio

        await limiter.check("client-a")
        await asyncio.sleep(0.02)  # cleanup interval elapses, window does not
        await limiter.check("client-a")

        self.assertIn("client-a", limiter._hits)
        self.assertEqual(len(limiter._hits["client-a"]), 2)


class ClientKeyTests(unittest.TestCase):
    @staticmethod
    def _request(
        peer: str = "10.0.0.10",
        forwarded_for: str | None = None,
    ) -> Request:
        headers = []
        if forwarded_for is not None:
            headers.append((b"x-forwarded-for", forwarded_for.encode("ascii")))
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/dna",
                "query_string": b"",
                "headers": headers,
                "client": (peer, 12345),
                "server": ("testserver", 80),
                "scheme": "http",
            }
        )

    def test_forwarded_header_is_ignored_by_default(self):
        request = self._request(forwarded_for="198.51.100.20")
        self.assertEqual(client_key(request, trusted_proxy_hops=0), "10.0.0.10")

    def test_fixed_hop_count_selects_from_trusted_right_side(self):
        request = self._request(
            forwarded_for="192.0.2.1, 198.51.100.20, 203.0.113.30"
        )
        self.assertEqual(
            client_key(request, trusted_proxy_hops=2),
            "198.51.100.20",
        )

    def test_untrusted_prefix_cannot_change_one_hop_selection(self):
        request = self._request(
            forwarded_for="192.0.2.99, 198.51.100.20"
        )
        self.assertEqual(
            client_key(request, trusted_proxy_hops=1),
            "198.51.100.20",
        )

    def test_invalid_or_short_forwarded_chain_falls_back_to_peer(self):
        invalid = self._request(forwarded_for="not-an-ip")
        short = self._request(forwarded_for="198.51.100.20")
        self.assertEqual(client_key(invalid, trusted_proxy_hops=1), "10.0.0.10")
        self.assertEqual(client_key(short, trusted_proxy_hops=2), "10.0.0.10")


if __name__ == "__main__":
    unittest.main()
