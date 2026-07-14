"""Privacy-safe exception and validation summaries."""
import unittest

import httpx
from pydantic import ValidationError

from error_safety import safe_exception_summary, validation_error_summary
from models import RecommendationItem


class ErrorSafetyTests(unittest.TestCase):
    def test_exception_summary_never_contains_message_text(self):
        secret = "private-reading-title"
        summary = safe_exception_summary(RuntimeError(secret))
        self.assertEqual(summary, "RuntimeError")
        self.assertNotIn(secret, summary)

    def test_http_summary_keeps_status_without_request_url(self):
        secret = "private-isbn"
        request = httpx.Request(
            "GET",
            f"https://example.invalid/search?identifier={secret}",
        )
        response = httpx.Response(503, request=request)
        error = httpx.HTTPStatusError("private response", request=request, response=response)
        summary = safe_exception_summary(error)
        self.assertEqual(summary, "HTTPStatusError (HTTP 503)")
        self.assertNotIn(secret, summary)
        self.assertNotIn("example.invalid", summary)

    def test_validation_summary_excludes_rejected_input(self):
        secret = "private-model-output"
        try:
            RecommendationItem.model_validate({"title": [secret]})
        except ValidationError as error:
            summary = validation_error_summary(error)
        else:
            self.fail("Expected recommendation validation to fail")

        self.assertIn("issue", summary)
        self.assertNotIn(secret, summary)
        self.assertNotIn("input_value", summary)


if __name__ == "__main__":
    unittest.main()
