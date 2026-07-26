"""Typed Pydantic models shared across the backend.

Centralizing these keeps request/response validation consistent and
replaces ad-hoc `dict` handling in the route handlers.
"""
import math
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from config import MAX_COLLECTION_SIZE, MAX_REVIEW_EXCERPT_CHARS


class Book(BaseModel):
    """A single book on a reader's shelf (read, currently-reading, DNF, or TBR)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(..., max_length=500)
    author: str = Field("", max_length=200)
    isbn: str = Field("", max_length=40)
    my_rating: int = Field(0, ge=0, le=5)
    avg_rating: float = Field(0.0, ge=0.0, le=5.0)
    num_pages: int = Field(0, ge=0, le=20000)
    year_published: str = Field("", max_length=20)
    date_read: str = Field("", max_length=40)
    year_read: Optional[int] = Field(None, ge=1000, le=2200)
    shelves: str = Field("", max_length=200)
    my_review: str = ""
    genres: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, v: object) -> str:
        # mode="before" so the Field(min-length-via-non-blank) check below
        # runs against the ALREADY-stripped value — otherwise a
        # whitespace-only string would pass Pydantic's own length check on
        # the raw value, then get silently stripped to "" by an "after"
        # validator, defeating the non-blank invariant.
        if not isinstance(v, str):
            raise ValueError("title must be a string")
        return v.strip()

    @field_validator("title")
    @classmethod
    def _title_nonblank(cls, v: str) -> str:
        if not v:
            raise ValueError("title must not be blank")
        return v

    @field_validator("author", "isbn", "year_published", "date_read", "shelves", mode="before")
    @classmethod
    def _strip(cls, v: object) -> str:
        # isinstance guard: a truthy non-string value (e.g. an int from a
        # malformed request) must 422 via Pydantic's own type coercion,
        # never raise an uncaught AttributeError from ``.strip()`` (-> 500).
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("must be a string")
        return v.strip()

    @field_validator("my_review", mode="before")
    @classmethod
    def _bound_review(cls, v: object) -> str:
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("review must be a string")
        v = v.strip()
        # Bound review length everywhere a Book flows through the app (prompts, storage).
        if len(v) > MAX_REVIEW_EXCERPT_CHARS:
            return v[:MAX_REVIEW_EXCERPT_CHARS]
        return v

    @field_validator("genres", mode="before")
    @classmethod
    def _bound_genres(cls, v: object) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError("genres must be a list of strings")
        cleaned: list[str] = []
        for genre in v:
            if not isinstance(genre, str):
                raise ValueError("each genre must be a string")
            genre = genre.strip()
            if not genre:
                continue
            if len(genre) > 100:
                raise ValueError("genre values must be 100 characters or fewer")
            cleaned.append(genre)
        return cleaned[:50]


class RatedBook(Book):
    """A completed/read shelf entry; unlike TBR/DNF/current books it must
    carry the reader's explicit 1-5 rating."""

    my_rating: int = Field(..., ge=1, le=5)


BookList = Field(default_factory=list, max_length=MAX_COLLECTION_SIZE)
RatedBookList = Field(default_factory=list, max_length=MAX_COLLECTION_SIZE)


class RSSRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    profile_url: str = Field(..., min_length=1, max_length=500)


class RecommendationItem(BaseModel):
    """A single LLM-produced recommendation, validated before being trusted."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(..., min_length=1, max_length=300)
    author: str = Field("", max_length=200)
    year: str = Field("", max_length=20)
    isbn: str = Field("", max_length=20)
    why: str = Field("", max_length=1000)
    # None (not defaulted to True) when the LLM simply omits the field, so
    # an omission is never silently presented to the reader as "comfort
    # zone: true" — that's a specific claim the model didn't actually make.
    comfort_zone: Optional[bool] = None
    hidden_gem: bool = False

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("title must be a string")
        value = v.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("author", "year", "isbn", "why", mode="before")
    @classmethod
    def _strip(cls, v: object) -> str:
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("must be a string")
        return v.strip()


class PredictionDriver(BaseModel):
    """One bounded, signed factor behind a rating prediction."""

    model_config = ConfigDict(extra="forbid", strict=True)

    factor: str = Field(..., min_length=1, max_length=200)
    direction: Literal["+", "-"]

    @field_validator("factor", mode="before")
    @classmethod
    def _strip_factor(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("factor must be a string")
        value = v.strip()
        if not value:
            raise ValueError("factor must not be blank")
        return value


class PredictionResponse(BaseModel):
    """Validated shape of a single model's prediction, before app-level meta is attached."""

    model_config = ConfigDict(extra="forbid", strict=True)

    predicted_rating: float = Field(..., ge=1.0, le=5.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    why: str = Field("", max_length=1000)
    drivers: list[PredictionDriver] = Field(default_factory=list, max_length=10)

    @field_validator("why", mode="before")
    @classmethod
    def _strip_why(cls, v: object) -> str:
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("why must be a string")
        return v.strip()


class TasteDimensions(BaseModel):
    """Numeric taste axes from the Reading DNA profile, all explicitly bounded."""

    model_config = ConfigDict(extra="forbid", strict=True)

    prose_density: float = Field(..., ge=1, le=10)
    pacing_preference: float = Field(..., ge=1, le=10)
    fiction_ratio: float = Field(..., ge=0, le=100)
    intellectual_depth: float = Field(..., ge=1, le=10)
    emotional_intensity: float = Field(..., ge=1, le=10)
    # Only meaningful when the profile has enough Goodreads-average-rating
    # consensus data; None means "signal unavailable", never a guess.
    contrarian_score: Optional[float] = Field(..., ge=1, le=10)


class TopBook(BaseModel):
    """One of the reader's most-loved books, enriched with a real ISBN when known."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(..., min_length=1, max_length=300)
    author: str = Field(..., max_length=200)
    why_loved: str = Field(..., min_length=1, max_length=500)
    isbn: str = Field("", max_length=40)

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("title must be a string")
        value = v.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("author", mode="before")
    @classmethod
    def _strip_author(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("author must be a string")
        return v.strip()

    @field_validator("isbn", mode="before")
    @classmethod
    def _strip_isbn(cls, v: object) -> str:
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("isbn must be a string or null")
        return v.strip()

    @field_validator("why_loved", mode="before")
    @classmethod
    def _strip_why_loved(cls, v: object) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("why_loved must be a non-blank string")
        return v.strip()


class DnaProfile(BaseModel):
    """The full, validated Reading DNA profile — the LLM's raw JSON plus the
    computed total_books/avg_rating fields, validated together so a
    malformed or out-of-range LLM response becomes an explicit error
    instead of a silently-accepted plausible-looking default."""

    model_config = ConfigDict(extra="forbid", strict=True)

    reader_archetype: str = Field(..., min_length=1, max_length=120)
    one_liner: str = Field(..., min_length=1, max_length=300)
    taste_dimensions: TasteDimensions
    top_themes: list[str] = Field(..., max_length=10)
    avoid_themes: list[str] = Field(..., max_length=10)
    favorite_authors: list[str] = Field(..., max_length=10)
    taste_summary: str = Field(..., min_length=1, max_length=1000)
    blind_spot_genres: list[str] = Field(..., max_length=10)
    top_books: list[TopBook] = Field(..., max_length=10)
    total_books: int = Field(..., ge=0)
    avg_rating: float = Field(..., ge=0.0, le=5.0)

    @field_validator("reader_archetype", mode="before")
    @classmethod
    def _strip_archetype(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("reader_archetype must be a string")
        value = v.strip()
        if not value:
            raise ValueError("reader_archetype must not be blank")
        return value

    @field_validator("one_liner", "taste_summary", mode="before")
    @classmethod
    def _strip(cls, v: object) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("must be a non-blank string")
        return v.strip()

    @field_validator("top_themes", "avoid_themes", "favorite_authors", "blind_spot_genres", mode="before")
    @classmethod
    def _bound_str_list(cls, v: object) -> list[str]:
        if not isinstance(v, list):
            raise ValueError("must be a list of strings")
        cleaned: list[str] = []
        for item in v:
            if not isinstance(item, str):
                raise ValueError("list entries must be strings")
            item = item.strip()
            if not item:
                continue
            if len(item) > 200:
                raise ValueError("list entries must be 200 characters or fewer")
            cleaned.append(item)
        return cleaned[:10]


class JudgeVerdictPayload(BaseModel):
    """A single judge's rubric scores + written verdict for one recommender's
    picks, validated before being trusted (Ollama's raw JSON is untrusted
    LLM output like any other)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    scores: dict[str, float] = Field(default_factory=dict)
    verdict: str = Field("", max_length=2000)

    @field_validator("scores", mode="before")
    @classmethod
    def _bound_scores(cls, v: object) -> dict[str, float]:
        if not isinstance(v, dict):
            raise ValueError("scores must be an object")
        bounded: dict[str, float] = {}
        for key, value in v.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("score keys must be non-blank strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"score for '{key}' is not numeric: {value!r}")
            score = float(value)
            if not math.isfinite(score):
                raise ValueError(f"score for '{key}' is not finite")
            if not (0 <= score <= 10):
                raise ValueError(f"score for '{key}' out of bounds 0-10: {score}")
            bounded[key.strip()] = score
        return bounded

    @field_validator("verdict")
    @classmethod
    def _strip_verdict(cls, v: str) -> str:
        return (v or "").strip()


class MapRecommendation(BaseModel):
    """A validated recommendation sent to the embedding/map endpoint."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(..., min_length=1, max_length=300)
    author: str = Field("", max_length=200)
    isbn: str = Field("", max_length=40)
    year: str | int | None = None
    why: str = Field("", max_length=1000)
    comfort_zone: Optional[bool] = None
    hidden_gem: bool = False
    on_tbr: bool = False
    model_name: str = Field("", max_length=100)
    genres: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("title", mode="before")
    @classmethod
    def _map_title(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("title must be a string")
        value = v.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("author", "isbn", "why", "model_name", mode="before")
    @classmethod
    def _map_strings(cls, v: object) -> str:
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("must be a string")
        return v.strip()

    @field_validator("year", mode="before")
    @classmethod
    def _map_year(cls, v: object) -> str | int | None:
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (str, int)):
            raise ValueError("year must be a string, integer, or null")
        if isinstance(v, str):
            value = v.strip()
            if len(value) > 20:
                raise ValueError("year must be 20 characters or fewer")
            return value or None
        return v

    @field_validator("genres", mode="before")
    @classmethod
    def _map_genres(cls, v: object) -> list[str]:
        return Book._bound_genres(v)


class BattleModelMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    latency_ms: int | float | None = Field(None, ge=0)
    ttft_ms: int | float | None = Field(None, ge=0)
    generation_ms: int | float | None = Field(None, ge=0)
    prompt_tokens: int | None = Field(None, ge=0)
    completion_tokens: int | None = Field(None, ge=0)


class BattleModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    display: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1, max_length=1000)
    architecture: str = Field(..., min_length=1, max_length=100)
    total_params: str = Field(..., min_length=1, max_length=100)
    active_params: str = Field(..., min_length=1, max_length=100)
    task_fit: str = Field(..., min_length=1, max_length=100)
    # Optional model-family / provider grouping metadata. Present on results
    # the backend emits (so the frontend can group an equivalent family's
    # entries into one cross-provider comparison) and therefore must be
    # accepted when the frontend echoes battle_results back to /judge.
    family: str | None = Field(None, min_length=1, max_length=100)
    family_display: str | None = Field(None, min_length=1, max_length=100)
    provider: str | None = Field(None, min_length=1, max_length=100)
    provider_display: str | None = Field(None, min_length=1, max_length=100)


class BattleModelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    recommendations: list[MapRecommendation] = Field(default_factory=list, max_length=10)
    meta: BattleModelMeta | None
    info: BattleModelInfo
    error: str | None = Field(None, max_length=1000)
    warnings: list[str] = Field(default_factory=list, max_length=50)


class BattleResultsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    models: dict[str, BattleModelPayload]
    rubric: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("models")
    @classmethod
    def _known_models(cls, v: dict[str, BattleModelPayload]) -> dict[str, BattleModelPayload]:
        # The battle is N-way across whichever providers are configured, so
        # the set of models is not a fixed pair. Require a non-empty payload
        # whose keys are all recognized model displays (from the provider
        # registry) — this rejects fabricated/unknown model names without
        # hardcoding a specific roster.
        from providers import KNOWN_MODEL_DISPLAYS

        if not v:
            raise ValueError("models must contain at least one model")
        unknown = sorted(set(v) - KNOWN_MODEL_DISPLAYS)
        if unknown:
            raise ValueError(f"models contains unknown model name(s): {unknown}")
        return v


class DnaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    books: list[RatedBook] = RatedBookList
    currently_reading: list[Book] = BookList
    dnf: list[Book] = BookList


class BattleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    dna_profile: DnaProfile
    books: list[RatedBook] = RatedBookList
    currently_reading: list[Book] = BookList
    dnf: list[Book] = BookList
    want_to_read: list[Book] = BookList


class EmbeddingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    books: list[RatedBook] = RatedBookList
    recommendations: list[MapRecommendation] = Field(default_factory=list, max_length=50)


class JudgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    dna_profile: DnaProfile
    battle_results: BattleResultsPayload


class LibbyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    isbns: list[str] = Field(default_factory=list, max_length=20)
    library_name: str = Field(..., min_length=1, max_length=200)

    @field_validator("isbns", mode="before")
    @classmethod
    def _trim_and_dedupe_isbns(cls, v: object) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError("isbns must be a list of strings")
        out: list[str] = []
        seen: set[str] = set()
        for isbn in v:
            if not isinstance(isbn, str):
                raise ValueError("each ISBN must be a string")
            cleaned = isbn.strip()
            if not cleaned or cleaned in seen:
                continue
            if len(cleaned) > 40:
                raise ValueError("ISBN values must be 40 characters or fewer")
            seen.add(cleaned)
            out.append(cleaned)
        return out


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(..., min_length=1, max_length=300)
    author: Optional[str] = Field(None, max_length=200)
    dna_profile: DnaProfile
    books: list[RatedBook] = RatedBookList

    @field_validator("title", mode="before")
    @classmethod
    def _predict_title(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("title must be a string")
        value = v.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("author", mode="before")
    @classmethod
    def _predict_author(cls, v: object) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("author must be a string")
        return v.strip() or None
