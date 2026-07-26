"""FastAPI application wiring: routes, CORS, request-size guard, rate limiting."""
import logging
import secrets

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

import config
from config import CEREBRAS_API_KEY, CORS_ORIGINS, MAX_JSON_BODY_BYTES, MAX_UPLOAD_BYTES
from dna import build_dna_profile
from embeddings import generate_embeddings_and_umap
from libby import check_availability
from llm_battle import run_battle, run_judge
from models import (
    BattleRequest,
    DnaRequest,
    EmbeddingsRequest,
    JudgeRequest,
    LibbyRequest,
    PredictRequest,
    RSSRequest,
)
from parsers import parse_csv_with_warnings, parse_rss
from predict import predict_rating
from rate_limit import (
    battle_limiter,
    client_key,
    csv_limiter,
    dna_limiter,
    embeddings_limiter,
    judge_limiter,
    libby_limiter,
    predict_limiter,
    rss_limiter,
)

logger = logging.getLogger(__name__)

app = FastAPI()


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Optional backend deployment control: when config.BACKEND_ACCESS_TOKEN
    is set, every route except OPTIONS (CORS preflight) and /health requires
    `Authorization: Bearer <token>`. Unset (the default) preserves today's
    open behavior. Reads config.BACKEND_ACCESS_TOKEN dynamically (via the
    module, not a bound name) on every request so it always reflects the
    currently configured value.

    This is a backend deployment control only — it is never exposed to or
    required of the frontend as a public secret.
    """

    EXEMPT_PATHS = {"/health"}

    async def dispatch(self, request: Request, call_next):
        token = config.BACKEND_ACCESS_TOKEN
        if not token or request.method == "OPTIONS" or request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        scheme, _, credential = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not credential or not secrets.compare_digest(credential, token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized: missing or invalid bearer token."},
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)


# Registration is deferred until all middleware classes are defined.

_CORS_OPTIONS = dict(
    allow_origins=CORS_ORIGINS,
    # Auth uses an Authorization: Bearer header (see BearerAuthMiddleware),
    # not cookies/session credentials — credentialed CORS is unnecessary
    # here, and enabling it would make a wildcard/broad CORS_ORIGINS
    # configuration an invalid (and browser-rejected) credentials setup.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class _RequestBodyTooLarge(Exception):
    pass


class MaxBodySizeMiddleware:
    """Reject request bodies larger than a configured cap.

    Uses the Content-Length header as a fast path when present. When it's
    absent (e.g. chunked transfer-encoding or HTTP/2), a receive wrapper
    counts each chunk while forwarding it unchanged to the downstream app.
    """

    def __init__(self, app, max_json_bytes: int, max_upload_bytes: int):
        self.app = app
        self.max_json_bytes = max_json_bytes
        self.max_upload_bytes = max_upload_bytes

    def _limit_for(self, path: str) -> int:
        return self.max_upload_bytes if path == "/parse/csv" else self.max_json_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope.get("path", ""))
        content_length = next(
            (
                value.decode("latin-1")
                for name, value in scope.get("headers", [])
                if name.lower() == b"content-length"
            ),
            None,
        )

        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                length = None
            if length is not None and length > limit:
                response = JSONResponse(
                    status_code=413,
                    content={"detail": f"Request body too large ({length} bytes, limit {limit})."},
                )
                await response(scope, receive, send)
                return

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            if response_started:
                raise
            response = JSONResponse(
                status_code=413,
                content={"detail": f"Request body too large (limit {limit} bytes)."},
            )
            await response(scope, receive, send)


# Starlette prepends each newly registered middleware, so this registration
# order produces CORS -> auth -> body-size guard -> route.
app.add_middleware(MaxBodySizeMiddleware, max_json_bytes=MAX_JSON_BODY_BYTES, max_upload_bytes=MAX_UPLOAD_BYTES)
app.add_middleware(BearerAuthMiddleware)
app.add_middleware(CORSMiddleware, **_CORS_OPTIONS)


@app.post("/parse/csv")
async def upload_csv(request: Request, file: UploadFile = File(...)):
    await csv_limiter.check(client_key(request))
    # Bounded read regardless of what Content-Length claims — protects
    # against a mismatched/missing header on multipart uploads.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"CSV file too large (limit {MAX_UPLOAD_BYTES} bytes).")
    books, warnings = parse_csv_with_warnings(content)
    return {"books": books, "count": len(books), "warnings": warnings}


@app.post("/parse/rss")
async def fetch_rss(req: RSSRequest, request: Request):
    await rss_limiter.check(client_key(request))
    result = await parse_rss(req.profile_url)
    return {
        "books": result["books"],
        "currently_reading": result["currently_reading"],
        "dnf": result["dnf"],
        "want_to_read": result["want_to_read"],
        "count": len(result["books"]),
        "warnings": result.get("warnings", []),
        "shelf_counts": result.get("shelf_counts", {}),
    }


@app.post("/dna")
async def build_dna(req: DnaRequest, request: Request):
    await dna_limiter.check(client_key(request))
    if not req.books:
        raise HTTPException(status_code=400, detail="No books provided")
    try:
        profile = await build_dna_profile(
            [b.model_dump() for b in req.books],
            [b.model_dump() for b in req.currently_reading],
            [b.model_dump() for b in req.dnf],
        )
    except (ValueError, RuntimeError) as e:
        # Malformed/invalid LLM JSON (fails DnaProfile validation) or a
        # model timeout are upstream-dependency failures, not a problem
        # with the client's request — a 502 (not 400) reflects that the
        # backend's own request was well-formed but the upstream LLM call
        # it depends on failed or produced unusable output.
        raise HTTPException(status_code=502, detail=str(e))
    return profile


@app.post("/battle")
async def llm_battle_endpoint(req: BattleRequest, request: Request):
    await battle_limiter.check(client_key(request))
    return await run_battle(
        req.dna_profile.model_dump(),
        [b.model_dump() for b in req.books],
        [b.model_dump() for b in req.currently_reading],
        [b.model_dump() for b in req.dnf],
        [b.model_dump() for b in req.want_to_read],
    )


@app.post("/judge")
async def judge_battle(req: JudgeRequest, request: Request):
    await judge_limiter.check(client_key(request))
    try:
        return await run_judge(
            req.dna_profile.model_dump(),
            req.battle_results.model_dump(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # Judge failures are real failures — surface them explicitly rather
        # than returning a success-shaped payload with a fabricated winner.
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/embeddings")
async def get_embeddings(req: EmbeddingsRequest, request: Request):
    await embeddings_limiter.check(client_key(request))
    return await generate_embeddings_and_umap(
        [b.model_dump() for b in req.books],
        [recommendation.model_dump() for recommendation in req.recommendations],
    )


@app.post("/libby")
async def libby_availability(req: LibbyRequest, request: Request):
    await libby_limiter.check(client_key(request))
    return await check_availability(req.isbns, req.library_name)


@app.post("/predict")
async def predict(req: PredictRequest, request: Request):
    await predict_limiter.check(client_key(request))
    return await predict_rating(
        req.title,
        req.author or "",
        req.dna_profile.model_dump(),
        [b.model_dump() for b in req.books],
    )


@app.get("/health")
def health():
    """Non-secret readiness indicators only — never return the key itself."""
    from providers import available_battle_models, display_for_model, provider_configured

    return {
        "status": "ok",
        "cerebras_configured": bool(CEREBRAS_API_KEY),
        "providers_configured": {
            "cerebras": provider_configured("cerebras"),
            "groq": provider_configured("groq"),
            "openrouter": provider_configured("openrouter"),
        },
        # The models that will actually compete in the battle given the
        # currently-configured provider keys (display names only, no secrets).
        "battle_models": [display_for_model(m) for m in available_battle_models()],
    }
