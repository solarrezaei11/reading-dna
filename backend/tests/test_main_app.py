"""Smoke tests for the FastAPI app wiring in main.py: route shape, removed
GET /predictions, non-secret health readiness reporting, /dna validation
error mapping, and the optional bearer-token deployment control.
"""
import json
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import config
import main


class AppRoutesTests(unittest.TestCase):
    VALID_DNA = {
        "reader_archetype": "Test",
        "one_liner": "A test reader.",
        "taste_dimensions": {
            "prose_density": 5,
            "pacing_preference": 5,
            "fiction_ratio": 50,
            "intellectual_depth": 5,
            "emotional_intensity": 5,
            "contrarian_score": None,
        },
        "top_themes": [],
        "avoid_themes": [],
        "favorite_authors": [],
        "taste_summary": "A complete test profile.",
        "blind_spot_genres": [],
        "top_books": [],
        "total_books": 1,
        "avg_rating": 4.0,
    }

    def setUp(self):
        self.client = TestClient(main.app)

    def test_get_predictions_endpoint_is_removed(self):
        # Requirement: no public GET /predictions exposing a global,
        # multi-user prediction log.
        resp = self.client.get("/predictions")
        self.assertEqual(resp.status_code, 404)

    def test_health_reports_non_secret_readiness_only(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("cerebras_configured", body)
        self.assertIsInstance(body["cerebras_configured"], bool)
        # The actual key must never be present in the response body.
        self.assertNotIn("CEREBRAS_API_KEY", str(body))

    def test_expected_post_routes_are_registered(self):
        paths = {r.path for r in main.app.routes}
        for expected in ("/parse/csv", "/parse/rss", "/dna", "/battle", "/judge", "/embeddings", "/libby", "/predict"):
            self.assertIn(expected, paths)

    def test_dna_endpoint_rejects_empty_books_with_400(self):
        resp = self.client.post("/dna", json={"books": []})
        self.assertEqual(resp.status_code, 400)

    def test_predict_endpoint_rejects_blank_title(self):
        resp = self.client.post(
            "/predict",
            json={"title": "   ", "dna_profile": self.VALID_DNA},
        )
        self.assertEqual(resp.status_code, 422)

    def test_request_models_reject_wrong_nested_types(self):
        cases = [
            ("/dna", {"books": [{"title": 123}]}),
            ("/battle", {"dna_profile": "not-an-object"}),
            ("/embeddings", {"recommendations": [{"author": "Missing Title"}]}),
            (
                "/judge",
                {
                    "dna_profile": self.VALID_DNA,
                    "battle_results": {
                        "models": {
                            "GPT-OSS 120B": [],
                            "GLM 4.7": [],
                        }
                    },
                },
            ),
            ("/libby", {"isbns": [123], "library_name": "Toronto"}),
            ("/predict", {"title": "Book", "author": 123, "dna_profile": self.VALID_DNA}),
        ]
        for path, payload in cases:
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json=payload).status_code, 422)

    def test_request_models_reject_unknown_fields(self):
        resp = self.client.post("/dna", json={"books": [], "unexpected": True})
        self.assertEqual(resp.status_code, 422)

    def test_oversized_json_body_rejected_with_413(self):
        # MAX_JSON_BODY_BYTES guard should reject a body that is clearly too
        # large before it reaches route validation logic.
        huge_title = "x" * 50
        resp = self.client.post(
            "/predict",
            json={"title": huge_title, "dna_profile": {"pad": "y" * (main.MAX_JSON_BODY_BYTES + 1000)}},
        )
        self.assertEqual(resp.status_code, 413)

    def test_parse_csv_route_surfaces_malformed_row_warnings(self):
        csv_text = (
            "Title,Author,My Rating\n"
            ",No Title Author,4\n"  # rated but missing title -> malformed, skipped
            "Real Book,Real Author,5\n"
        )
        resp = self.client.post(
            "/parse/csv",
            files={"file": ("export.csv", csv_text.encode("utf-8"), "text/csv")},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["count"], 1)
        self.assertIn("warnings", body)
        self.assertEqual(len(body["warnings"]), 1)

    def test_parse_csv_route_has_no_warnings_for_clean_file(self):
        csv_text = "Title,Author,My Rating\nGood Book,Good Author,5\n"
        resp = self.client.post(
            "/parse/csv",
            files={"file": ("export.csv", csv_text.encode("utf-8"), "text/csv")},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["warnings"], [])

    def test_dna_endpoint_maps_upstream_value_error_to_502(self):
        async def fake_build_dna_profile(*args, **kwargs):
            raise ValueError("Reading DNA response failed validation: bad shape")

        with mock.patch.object(main, "build_dna_profile", side_effect=fake_build_dna_profile):
            resp = self.client.post(
                "/dna",
                json={"books": [{"title": "A Book", "author": "An Author", "my_rating": 5}]},
            )
        self.assertEqual(resp.status_code, 502)
        self.assertIn("failed validation", resp.json()["detail"])


class BearerAuthMiddlewareTests(unittest.TestCase):
    """config.BACKEND_ACCESS_TOKEN unset (default) preserves today's open
    behavior; when set, non-exempt routes require a matching bearer token."""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_unset_token_preserves_open_behavior(self):
        with mock.patch.object(config, "BACKEND_ACCESS_TOKEN", None):
            resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_missing_authorization_header_is_rejected_when_token_configured(self):
        with mock.patch.object(config, "BACKEND_ACCESS_TOKEN", "s3cret"):
            resp = self.client.post("/dna", json={"books": []})
        self.assertEqual(resp.status_code, 401)

    def test_wrong_token_is_rejected(self):
        with mock.patch.object(config, "BACKEND_ACCESS_TOKEN", "s3cret"):
            resp = self.client.post(
                "/dna", json={"books": []}, headers={"Authorization": "Bearer wrong-token"}
            )
        self.assertEqual(resp.status_code, 401)

    def test_correct_token_is_accepted(self):
        with mock.patch.object(config, "BACKEND_ACCESS_TOKEN", "s3cret"):
            # Empty books still yields the normal 400 from route logic, but
            # this proves the request got PAST auth (not blocked with 401).
            resp = self.client.post(
                "/dna", json={"books": []}, headers={"Authorization": "Bearer s3cret"}
            )
        self.assertEqual(resp.status_code, 400)

    def test_health_is_always_exempt_even_with_token_configured(self):
        with mock.patch.object(config, "BACKEND_ACCESS_TOKEN", "s3cret"):
            resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_options_preflight_is_always_exempt(self):
        with mock.patch.object(config, "BACKEND_ACCESS_TOKEN", "s3cret"):
            resp = self.client.options(
                "/dna",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "POST",
                },
            )
        self.assertNotEqual(resp.status_code, 401)

    def test_auth_runs_before_large_body_buffering_and_cors_wraps_error(self):
        with mock.patch.object(config, "BACKEND_ACCESS_TOKEN", "s3cret"):
            resp = self.client.post(
                "/predict",
                json={"title": "x", "padding": "y" * (main.MAX_JSON_BODY_BYTES + 1000)},
                headers={"Origin": "http://localhost:3000"},
            )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "http://localhost:3000")


class MaxBodySizeMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _scope():
        return {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/echo",
            "raw_path": b"/echo",
            "query_string": b"",
            "headers": [],
            "client": ("test", 1),
            "server": ("test", 80),
        }

    @staticmethod
    async def _echo_app(scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        response = main.JSONResponse({"body": body.decode("utf-8")})
        await response(scope, receive, send)

    async def _invoke(self, chunks, limit):
        incoming = [
            {
                "type": "http.request",
                "body": chunk,
                "more_body": index < len(chunks) - 1,
            }
            for index, chunk in enumerate(chunks)
        ]
        sent = []

        async def receive():
            return incoming.pop(0)

        async def send(message):
            sent.append(message)

        middleware = main.MaxBodySizeMiddleware(
            self._echo_app,
            max_json_bytes=limit,
            max_upload_bytes=limit,
        )
        await middleware(self._scope(), receive, send)
        status = next(message["status"] for message in sent if message["type"] == "http.response.start")
        raw_body = b"".join(
            message.get("body", b"") for message in sent if message["type"] == "http.response.body"
        )
        return status, json.loads(raw_body)

    async def test_chunked_body_is_forwarded_unchanged(self):
        status, body = await self._invoke([b"abc", b"def"], limit=10)
        self.assertEqual(status, 200)
        self.assertEqual(body["body"], "abcdef")

    async def test_chunked_body_over_limit_is_rejected(self):
        status, body = await self._invoke([b"abc", b"def"], limit=5)
        self.assertEqual(status, 413)
        self.assertIn("too large", body["detail"].lower())


if __name__ == "__main__":
    unittest.main()
