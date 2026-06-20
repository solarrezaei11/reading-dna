import os
import json
import asyncio
import time
from cerebras.cloud.sdk import AsyncCerebras

client = AsyncCerebras(api_key=os.environ.get("CEREBRAS_API_KEY"))

MODEL_INFO = {
    "gpt-oss-120b": {
        "display": "GPT-OSS 120B",
        "description": "Open-source 120B parameter model via Cerebras wafer-scale chip. Broad knowledge, strong reasoning.",
    },
    "zai-glm-4.7": {
        "display": "GLM 4.7",
        "description": "ZhipuAI's GLM-4 series model. Strong multilingual and creative reasoning capabilities.",
    },
}

RUBRIC = {
    "relevance": "How well do the picks match the reader's stated taste dimensions and themes? (0-10)",
    "diversity": "How diverse are the picks across sub-genres, time periods, and authors? (0-10)",
    "reasoning_quality": "How specific and insightful is the reasoning for each pick? (0-10)",
    "novelty": "How surprising/non-obvious are the picks vs. what the reader likely already knows? (0-10)",
    "serendipity": "Does at least one pick meaningfully push the reader outside their comfort zone in a good way? (0-10)",
}


def build_battle_prompt(dna: dict, books: list[dict], currently_reading: list[dict] = [], dnf: list[dict] = []) -> str:
    read_titles = [b["title"] for b in books[:100]]
    dnf_titles = [b["title"] for b in dnf]
    cr_titles = [b["title"] for b in currently_reading]
    all_exclude = list(set(read_titles + dnf_titles + cr_titles))

    dnf_note = f"\nBooks they started but did NOT finish (do NOT recommend these — something didn't click):\n{', '.join(dnf_titles)}" if dnf_titles else ""
    cr_note = f"\nCurrently reading (do NOT recommend these — they already have them):\n{', '.join(cr_titles)}" if cr_titles else ""

    return f"""You are recommending books to a specific reader. Here is their Reading DNA profile:

Archetype: {dna.get('reader_archetype')}
Taste summary: {dna.get('taste_summary')}
Top themes they love: {', '.join(dna.get('top_themes', []))}
Themes to avoid: {', '.join(dna.get('avoid_themes', []))}
Prose density preference: {dna.get('taste_dimensions', {}).get('prose_density')}/10
Pacing preference: {dna.get('taste_dimensions', {}).get('pacing_preference')}/10
Intellectual depth: {dna.get('taste_dimensions', {}).get('intellectual_depth')}/10

Books they've already read (do NOT recommend these):
{', '.join(read_titles[:60])}{dnf_note}{cr_note}

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
      "comfort_zone": true
    }}
  ]
}}

Set comfort_zone to false for any pick that intentionally pushes them outside their usual taste.
Include at least 1 comfort_zone=false pick."""


async def call_model(model: str, prompt: str) -> dict:
    t0 = time.time()
    ttft: float | None = None
    chunks: list[str] = []
    prompt_tokens = completion_tokens = None

    stream = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a book recommendation expert. Always respond with valid JSON only, no markdown."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.8,
        stream=True,
    )

    async for chunk in stream:
        if ttft is None:
            ttft = time.time()
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            chunks.append(delta)
        if chunk.usage:
            prompt_tokens = chunk.usage.prompt_tokens
            completion_tokens = chunk.usage.completion_tokens

    t_end = time.time()
    ttft_ms = round((ttft - t0) * 1000) if ttft else None
    generation_ms = round((t_end - ttft) * 1000) if ttft else None
    total_ms = round((t_end - t0) * 1000)

    text = "".join(chunks).strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text.strip())
    data["_meta"] = {
        "latency_ms": total_ms,
        "ttft_ms": ttft_ms,
        "generation_ms": generation_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    return data


async def run_battle(dna: dict, books: list[dict], currently_reading: list[dict] = [], dnf: list[dict] = []) -> dict:
    prompt = build_battle_prompt(dna, books, currently_reading, dnf)

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
    for model_id, (name, recs) in zip(["gpt-oss-120b", "zai-glm-4.7"], models.items()):
        info = MODEL_INFO.get(model_id, {})
        if isinstance(recs, Exception):
            results[name] = {"error": str(recs), "recommendations": [], "meta": None, "info": info}
        else:
            meta = recs.pop("_meta", {})
            results[name] = {
                "recommendations": recs.get("recommendations", []),
                "meta": meta,
                "info": info,
            }

    return {"models": results, "winner": None, "rubric": RUBRIC}
