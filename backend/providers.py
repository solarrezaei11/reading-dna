"""Provider + model registry for the multi-provider recommendation battle.

The battle historically ran two Cerebras-hosted models head-to-head. This
module generalizes that to an N-way battle across multiple *free-tier*
providers (Cerebras, Groq, OpenRouter). A model only ever participates when
its provider's API key is actually configured, so the app still runs with
just a CEREBRAS_API_KEY set (identical to the original behavior) and
transparently gains extra competitors the moment a Groq or OpenRouter key is
added — nothing else has to change.

Kept dependency-free of models.py so both llm_battle.py and models.py can
import it without a circular import. It only depends on config/os, mirroring
the "lazy, optional SDK" philosophy of the rest of the backend: Groq and
OpenRouter are OpenAI-compatible and are called over plain httpx SSE (already
a dependency), so no extra SDK is required to add them.
"""
import os

from config import (
    CEREBRAS_API_KEY,
    GROQ_API_KEY,
    GROQ_BATTLE_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_BATTLE_MODEL,
)

# GROQ_BATTLE_MODEL / OPENROUTER_BATTLE_MODEL remain importable for backward
# compatibility (older .env files set them) but the roster below now pins the
# provider-specific model IDs explicitly so it can run the *same* logical
# model on multiple providers for a true apples-to-apples speed comparison.
_ = (GROQ_BATTLE_MODEL, OPENROUTER_BATTLE_MODEL)

# --- Provider registry ----------------------------------------------------
# sdk="cerebras" uses the AsyncCerebras SDK (already used by dna/embeddings);
# sdk="openai_http" is any OpenAI-compatible /chat/completions endpoint we
# stream over raw httpx SSE (Groq, OpenRouter, and anything else compatible).
PROVIDERS: dict[str, dict] = {
    "cerebras": {
        "display": "Cerebras",
        "sdk": "cerebras",
        "api_key_env": "CEREBRAS_API_KEY",
        "base_url": None,  # the SDK knows its own base URL
    },
    "groq": {
        "display": "Groq",
        "sdk": "openai_http",
        "api_key_env": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
    },
    "openrouter": {
        "display": "OpenRouter",
        "sdk": "openai_http",
        "api_key_env": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
    },
}


def _provider_api_key(provider: str) -> str:
    """Resolve a provider's API key, preferring the value already parsed into
    config at import time but falling back to a live env read (so a key set
    after import — e.g. in a test — is still honored)."""
    cfg = PROVIDERS[provider]
    cached = {
        "cerebras": CEREBRAS_API_KEY,
        "groq": GROQ_API_KEY,
        "openrouter": OPENROUTER_API_KEY,
    }.get(provider, "")
    return (cached or os.environ.get(cfg["api_key_env"], "") or "").strip()


def provider_configured(provider: str) -> bool:
    return bool(_provider_api_key(provider))


# --- Model registry -------------------------------------------------------
# The battle is organized around *model families* (a logical model such as
# "GPT-OSS 120B") that can be served by more than one provider. Running the
# SAME family on multiple providers is what makes the speed numbers an
# apples-to-apples comparison of the providers' inference stacks (Cerebras
# wafer-scale vs Groq LPU vs OpenRouter's routed free tier) rather than a
# comparison of different models.
#
# Each battle "entry" is one (family, provider) pairing with its own unique
# `key` (the token that flows through call_model/run_battle and keys the
# results dict). An entry only participates when its provider's API key is
# configured, so a Cerebras-only deployment still runs — it just won't have a
# cross-provider opponent for any family.
#
# `key` convention: Cerebras entries keep the bare model id (so predict.py,
# which passes "gpt-oss-120b"/"zai-glm-4.7", keeps working unchanged); every
# other provider's entry is namespaced "{provider}:{api_model}" so the same
# api_model on two providers never collides.


def _entry(
    provider: str,
    api_model: str,
    family: str,
    family_display: str,
    description: str,
    architecture: str,
    total_params: str,
    active_params: str,
    task_fit: str,
    key: str | None = None,
    max_tokens: int | None = None,
) -> dict:
    provider_display = PROVIDERS[provider]["display"]
    return {
        "key": key or (api_model if provider == "cerebras" else f"{provider}:{api_model}"),
        "provider": provider,
        "provider_display": provider_display,
        "api_model": api_model,
        "family": family,
        "family_display": family_display,
        # Unique, human-readable label used as the results-dict key and the
        # frontend card title. Includes the provider so the two entries of an
        # equivalent family are distinguishable.
        "display": f"{family_display} · {provider_display}",
        "description": description,
        "architecture": architecture,
        "total_params": total_params,
        "active_params": active_params,
        "task_fit": task_fit,
        # Optional per-entry output-token cap. Overrides the global
        # BATTLE_MAX_COMPLETION_TOKENS default when a provider's free tier
        # meters requested tokens more tightly (e.g. Groq's free-tier TPM for
        # gpt-oss-120b is only 8000, so prompt + max_tokens must stay under it).
        "max_tokens": max_tokens,
    }


_GPT_OSS_120B_DESC = (
    "GPT-OSS 120B — open-weight reasoning MoE, 117B total / 5.1B active parameters per token, "
    "128 experts. Here it runs on both Cerebras and Groq so the numbers isolate the inference "
    "provider, not the model. TTFT is observed response-start latency for this run only."
)
_GEMMA_31B_DESC = (
    "Gemma 4 31B — dense instruction-tuned model (no separate reasoning phase). Runs on both "
    "Cerebras and OpenRouter's free tier here, so the gap is the provider's serving stack, not "
    "the model. TTFT is observed response-start latency for this run only."
)

# Ordered so Cerebras models come first (the two predict.py models lead,
# preserving the original battle order), then each cross-provider opponent.
_ROSTER: list[dict] = [
    _entry(
        provider="cerebras", api_model="gpt-oss-120b",
        family="gpt-oss-120b", family_display="GPT-OSS 120B",
        description=_GPT_OSS_120B_DESC,
        architecture="MoE", total_params="117B", active_params="5.1B", task_fit="reasoning",
    ),
    _entry(
        provider="cerebras", api_model="zai-glm-4.7",
        family="glm-4.7", family_display="GLM 4.7",
        description=(
            "ZhipuAI's GLM-4 series — MoE, 355B total / 32B active parameters per token. Cerebras "
            "is the only configured provider that serves it, so it competes without a cross-provider "
            "opponent. TTFT is observed response-start latency for this run only."
        ),
        architecture="MoE", total_params="355B", active_params="32B", task_fit="interactive",
    ),
    _entry(
        provider="cerebras", api_model="gemma-4-31b",
        family="gemma-4-31b", family_display="Gemma 4 31B",
        description=_GEMMA_31B_DESC,
        architecture="dense", total_params="31B", active_params="31B", task_fit="interactive",
    ),
    _entry(
        provider="groq", api_model="openai/gpt-oss-120b",
        family="gpt-oss-120b", family_display="GPT-OSS 120B",
        description=_GPT_OSS_120B_DESC,
        architecture="MoE", total_params="117B", active_params="5.1B", task_fit="reasoning",
        # Groq's free-tier TPM for gpt-oss-120b is 8000; keep prompt + output
        # comfortably under it (gpt-oss produces ~1.5k tokens for this task).
        max_tokens=5000,
    ),
    _entry(
        provider="openrouter", api_model="google/gemma-4-31b-it:free",
        family="gemma-4-31b", family_display="Gemma 4 31B",
        description=_GEMMA_31B_DESC,
        architecture="dense", total_params="31B", active_params="31B", task_fit="interactive",
    ),
]

# Registry keyed by unique entry key.
MODEL_REGISTRY: dict[str, dict] = {entry["key"]: entry for entry in _ROSTER}

# Deterministic default battle order (roster order).
_DEFAULT_MODEL_ORDER: list[str] = [entry["key"] for entry in _ROSTER]

# BattleModelInfo-shaped info (everything the frontend may render), keyed by
# entry key. Includes family/provider metadata so the frontend can group an
# equivalent family's entries into a single cross-provider comparison.
_INFO_FIELDS = (
    "display", "description", "architecture", "total_params", "active_params",
    "task_fit", "family", "family_display", "provider", "provider_display",
)
MODEL_INFO: dict[str, dict] = {
    key: {field: entry[field] for field in _INFO_FIELDS}
    for key, entry in MODEL_REGISTRY.items()
}

# Every model display the backend can legitimately emit — used by models.py to
# validate an inbound battle_results payload without hardcoding a fixed pair.
KNOWN_MODEL_DISPLAYS: set[str] = {entry["display"] for entry in _ROSTER}


def provider_for_model(model_id: str) -> str:
    """Provider a battle entry runs on. Unknown keys default to 'cerebras',
    preserving the original single-provider assumption for any caller that
    passes an ad-hoc model id (e.g. tests using 'fake-model')."""
    entry = MODEL_REGISTRY.get(model_id)
    return entry["provider"] if entry else "cerebras"


def api_model_for(model_id: str) -> str:
    """The provider-specific model id to send on the wire for a battle entry.
    Unknown keys fall back to themselves so ad-hoc/legacy ids (and predict's
    bare Cerebras ids) still address the right model."""
    entry = MODEL_REGISTRY.get(model_id)
    return entry["api_model"] if entry else model_id


def max_tokens_for(model_id: str) -> int | None:
    """Per-entry output-token cap override, or None to use the global default.
    Used to keep providers with tighter free-tier token metering (e.g. Groq's
    8000 TPM for gpt-oss-120b) from rejecting the request."""
    entry = MODEL_REGISTRY.get(model_id)
    return entry.get("max_tokens") if entry else None


def display_for_model(model_id: str) -> str:
    entry = MODEL_REGISTRY.get(model_id)
    return entry["display"] if entry else model_id


def available_battle_models() -> list[str]:
    """The entry keys that should compete this run: every roster entry whose
    provider's API key is configured, in deterministic roster order. With only
    CEREBRAS_API_KEY set this returns just the Cerebras entries; adding a Groq
    or OpenRouter key transparently adds each configured provider's entries —
    including the cross-provider opponents for the shared families."""
    return [
        key
        for key in _DEFAULT_MODEL_ORDER
        if provider_configured(provider_for_model(key))
    ]
