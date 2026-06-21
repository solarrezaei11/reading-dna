import os
import json
import asyncio
from cerebras.cloud.sdk import AsyncCerebras

client = AsyncCerebras(api_key=os.environ.get("CEREBRAS_API_KEY"))


def build_book_summary(books: list[dict]) -> str:
    rated = sorted(books, key=lambda b: b["my_rating"], reverse=True)
    lines = []
    for b in rated[:80]:
        lines.append(f'{b["my_rating"]}/5 — "{b["title"]}" by {b["author"]} ({b["year_published"] or "?"})')
    return "\n".join(lines)


async def build_dna_profile(books: list[dict], currently_reading: list[dict] = [], dnf: list[dict] = []) -> dict:
    summary = build_book_summary(books)
    total = len(books)
    avg = sum(b["my_rating"] for b in books) / total if total else 0
    high_rated = [b for b in books if b["my_rating"] >= 4]
    low_rated = [b for b in books if b["my_rating"] <= 2]

    currently_reading_section = ""
    if currently_reading:
        titles = ", ".join(f'"{b["title"]}" by {b["author"]}' for b in currently_reading[:10])
        currently_reading_section = f"\nCURRENTLY READING (strong active interest signal — these grabbed them enough to start):\n{titles}\n"

    dnf_section = ""
    if dnf:
        titles = ", ".join(f'"{b["title"]}" by {b["author"]}' for b in dnf[:10])
        dnf_section = f"\nDID NOT FINISH (engagement/friction signal — something didn't hold their attention; NOT a dislike signal, more about pacing, style, or timing):\n{titles}\n"

    prompt = f"""You are a literary analyst. Analyze this reader's Goodreads history and return a structured JSON Reading DNA profile.

RATED BOOKS (up to 80, sorted by their rating):
{summary}

Total books rated: {total}
Average rating given: {avg:.2f}/5
Books rated 4-5 stars: {len(high_rated)}
Books rated 1-2 stars: {len(low_rated)}
{currently_reading_section}{dnf_section}
Use currently-reading books as a signal of what actively excites them right now.
Use DNF books as a subtle friction signal about what styles or pacing didn't sustain their engagement — NOT as dislikes.

Return ONLY valid JSON with this exact structure, no markdown fences:
{{
  "reader_archetype": "A short evocative label (e.g. 'The Melancholic Intellectual', 'The Escapist Adventurer')",
  "one_liner": "One sentence describing this reader's taste personality",
  "taste_dimensions": {{
    "prose_density": <1-10, 1=breezy 10=dense/literary>,
    "pacing_preference": <1-10, 1=slow-burn 10=fast-paced>,
    "fiction_ratio": <0-100 percent fiction estimate>,
    "intellectual_depth": <1-10>,
    "emotional_intensity": <1-10>,
    "contrarian_score": <1-10, how often they rate against popular consensus>
  }},
  "top_themes": ["theme1", "theme2", "theme3", "theme4", "theme5"],
  "avoid_themes": ["theme1", "theme2"],
  "favorite_authors": ["author1", "author2", "author3"],
  "taste_summary": "2-3 sentences describing what this reader loves and why, written in second person (You...)",
  "blind_spot_genres": ["genre1", "genre2"],
  "top_books": [
    {{"title": "...", "author": "...", "why_loved": "one sentence"}}
  ]
}}

top_books should be the 3 most loved books (5-star or highest rated).
blind_spot_genres are genres that critically acclaimed readers with similar taste often love but this reader hasn't explored."""

    resp = await client.chat.completions.create(
        model="gpt-oss-120b",
        messages=[
            {"role": "system", "content": "You are a literary analyst. Always respond with valid JSON only, no markdown."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )
    text = resp.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    profile = json.loads(text.strip())
    profile["total_books"] = total
    profile["avg_rating"] = round(avg, 2)
    return profile
