"""Tests for parsers.validate_and_normalize_profile_input (SSRF hardening)
and parsers.resolve_numeric_user_id's bounded (not minimum-length) numeric
ID handling."""
import unittest

import httpx
from fastapi import HTTPException

import parsers


class ValidateProfileInputTests(unittest.TestCase):
    def test_numeric_id_passes_through_unchanged(self):
        self.assertEqual(parsers.validate_and_normalize_profile_input("123456789"), "123456789")

    def test_numeric_id_with_surrounding_whitespace(self):
        self.assertEqual(parsers.validate_and_normalize_profile_input("  123456  "), "123456")

    def test_short_numeric_id_from_an_early_account_is_accepted(self):
        # Early Goodreads accounts can have very short numeric user IDs —
        # there is no minimum digit count; security comes from host/path
        # canonicalization, not digit-count heuristics.
        self.assertEqual(parsers.validate_and_normalize_profile_input("1"), "1")
        self.assertEqual(parsers.validate_and_normalize_profile_input("42"), "42")
        self.assertEqual(parsers.validate_and_normalize_profile_input("999"), "999")

    def test_twenty_digit_numeric_id_is_accepted(self):
        twenty_digits = "1" + "0" * 19
        self.assertEqual(parsers.validate_and_normalize_profile_input(twenty_digits), twenty_digits)

    def test_zero_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            parsers.validate_and_normalize_profile_input("0")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_zero_padded_to_multiple_digits_is_rejected(self):
        with self.assertRaises(HTTPException):
            parsers.validate_and_normalize_profile_input("0000")

    def test_more_than_twenty_digits_is_rejected(self):
        too_long = "1" * 21
        with self.assertRaises(HTTPException) as ctx:
            parsers.validate_and_normalize_profile_input(too_long)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_bare_host_path_gets_https_scheme_and_www_canonicalization(self):
        # Bare "goodreads.com" is normalized to "www.goodreads.com" — the
        # host is always canonicalized, never left as the bare variant.
        result = parsers.validate_and_normalize_profile_input("goodreads.com/user/show/123-jane")
        self.assertEqual(result, "https://www.goodreads.com/user/show/123-jane")

    def test_www_subdomain_allowed(self):
        result = parsers.validate_and_normalize_profile_input("http://www.goodreads.com/user/show/123-jane")
        self.assertEqual(result, "https://www.goodreads.com/user/show/123-jane")

    def test_https_input_stays_https(self):
        result = parsers.validate_and_normalize_profile_input("https://www.goodreads.com/review/list/123")
        self.assertEqual(result, "https://www.goodreads.com/review/list/123")

    def test_query_string_is_dropped(self):
        result = parsers.validate_and_normalize_profile_input("https://www.goodreads.com/review/list/123?shelf=read")
        self.assertEqual(result, "https://www.goodreads.com/review/list/123")

    def test_rejects_unrelated_host(self):
        with self.assertRaises(HTTPException) as ctx:
            parsers.validate_and_normalize_profile_input("http://evil.example.com/internal")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_lookalike_host(self):
        # A host that merely contains "goodreads.com" as a substring must not pass.
        with self.assertRaises(HTTPException):
            parsers.validate_and_normalize_profile_input("http://goodreads.com.evil.example.com/x")

    def test_rejects_non_profile_path_on_real_host(self):
        # An arbitrary path on the real goodreads.com host is still
        # rejected — only /user/show/... and /review/list/... are actual
        # profile paths; anything else must not become a redirect-following
        # fetch of an unrelated goodreads.com page.
        with self.assertRaises(HTTPException) as ctx:
            parsers.validate_and_normalize_profile_input("https://www.goodreads.com/some/other/page")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_book_show_path(self):
        with self.assertRaises(HTTPException):
            parsers.validate_and_normalize_profile_input("https://www.goodreads.com/book/show/12345")

    def test_rejects_bare_host_with_no_path(self):
        with self.assertRaises(HTTPException):
            parsers.validate_and_normalize_profile_input("https://www.goodreads.com")

    def test_rejects_non_http_scheme(self):
        with self.assertRaises(HTTPException):
            parsers.validate_and_normalize_profile_input("file:///etc/passwd")

    def test_rejects_ftp_scheme(self):
        with self.assertRaises(HTTPException):
            parsers.validate_and_normalize_profile_input("ftp://goodreads.com/x")

    def test_rejects_userinfo_host_confusion_ssrf(self):
        # goodreads.com is placed as userinfo; the real host is an internal/
        # metadata-service address. hostname parsing must see the real host.
        with self.assertRaises(HTTPException):
            parsers.validate_and_normalize_profile_input("http://goodreads.com@169.254.169.254/latest/meta-data")

    def test_rejects_empty_input(self):
        with self.assertRaises(HTTPException):
            parsers.validate_and_normalize_profile_input("")

    def test_rejects_whitespace_only_input(self):
        with self.assertRaises(HTTPException):
            parsers.validate_and_normalize_profile_input("   ")


class ResolveNumericUserIdBoundedDigitsTests(unittest.IsolatedAsyncioTestCase):
    """resolve_numeric_user_id must extract a bounded (1-20 digit, non-zero)
    numeric ID from a canonical profile URL without requiring a minimum
    digit count — short IDs from early accounts must resolve correctly."""

    async def test_short_id_extracted_directly_from_user_show_url_without_network_call(self):
        # If this fell through to the HTTP fallback it would hang/fail since
        # no transport is configured — a passing result here proves the
        # short ID was matched directly from the URL.
        client = httpx.AsyncClient()
        try:
            result = await parsers.resolve_numeric_user_id(
                "https://www.goodreads.com/user/show/1-jane", client
            )
        finally:
            await client.aclose()
        self.assertEqual(result, "1")

    async def test_short_id_extracted_directly_from_review_list_url(self):
        client = httpx.AsyncClient()
        try:
            result = await parsers.resolve_numeric_user_id(
                "https://www.goodreads.com/review/list/7", client
            )
        finally:
            await client.aclose()
        self.assertEqual(result, "7")

    async def test_short_id_extracted_from_fetched_html_fallback(self):
        # No numeric ID directly in the URL path (a bare profile URL with no
        # id) -> falls back to fetching the page and scanning for a short
        # embedded "user_id" field.
        def handler(request):
            return httpx.Response(200, text='some page html ... "user_id": 3 ... more html')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await parsers.resolve_numeric_user_id("https://www.goodreads.com/somejane", client)
        self.assertEqual(result, "3")

    async def test_zero_embedded_in_url_is_not_treated_as_a_valid_id(self):
        # A literal "/0" segment must not resolve to user id "0" — falls
        # through to the (here failing, since no id appears anywhere) HTML
        # fallback rather than accepting zero.
        def handler(request):
            return httpx.Response(200, text="no id here at all")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(HTTPException):
                await parsers.resolve_numeric_user_id("https://www.goodreads.com/user/show/0", client)

    async def test_same_host_https_redirect_is_followed(self):
        def handler(request):
            if request.url.path == "/user/show/profile-name":
                return httpx.Response(302, headers={"Location": "/user/show/42-profile-name"})
            return httpx.Response(200, text='"user_id": 42')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await parsers.resolve_numeric_user_id(
                "https://www.goodreads.com/user/show/profile-name",
                client,
            )
        self.assertEqual(result, "42")

    async def test_cross_host_redirect_is_rejected_without_following(self):
        requests_seen = []

        def handler(request):
            requests_seen.append(str(request.url))
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(HTTPException):
                await parsers.resolve_numeric_user_id(
                    "https://www.goodreads.com/user/show/profile-name",
                    client,
                )
        self.assertEqual(len(requests_seen), 1)


if __name__ == "__main__":
    unittest.main()
