import os
import asyncio
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import httpx

from parsers import parse_csv, parse_rss
from dna import build_dna_profile
from llm_battle import run_battle, run_judge
from embeddings import generate_embeddings_and_umap
from libby import check_availability

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class RSSRequest(BaseModel):
    profile_url: str

class BattleRequest(BaseModel):
    dna_profile: dict
    books: list[dict]
    currently_reading: list[dict] = []
    dnf: list[dict] = []
    want_to_read: list[dict] = []

class EmbeddingsRequest(BaseModel):
    books: list[dict]
    recommendations: list[dict] = []

class JudgeRequest(BaseModel):
    dna_profile: dict
    battle_results: dict

class LibbyRequest(BaseModel):
    isbns: list[str]
    library_name: str

@app.post("/parse/csv")
async def upload_csv(file: UploadFile = File(...)):
    content = await file.read()
    books = parse_csv(content.decode("utf-8"))
    return {"books": books, "count": len(books)}

@app.post("/parse/rss")
async def fetch_rss(req: RSSRequest):
    result = await parse_rss(req.profile_url)
    return {
        "books": result["books"],
        "currently_reading": result["currently_reading"],
        "dnf": result["dnf"],
        "want_to_read": result["want_to_read"],
        "count": len(result["books"]),
    }

@app.post("/dna")
async def build_dna(req: dict):
    books = req.get("books", [])
    if not books:
        raise HTTPException(status_code=400, detail="No books provided")
    currently_reading = req.get("currently_reading", [])
    dnf = req.get("dnf", [])
    profile = await build_dna_profile(books, currently_reading, dnf)
    return profile

@app.post("/battle")
async def llm_battle(req: BattleRequest):
    results = await run_battle(req.dna_profile, req.books, req.currently_reading, req.dnf, req.want_to_read)
    return results

@app.post("/judge")
async def judge_battle(req: JudgeRequest):
    return await run_judge(req.dna_profile, req.battle_results)

@app.post("/embeddings")
async def get_embeddings(req: EmbeddingsRequest):
    result = await generate_embeddings_and_umap(req.books, req.recommendations)
    return result

@app.post("/libby")
async def libby_availability(req: LibbyRequest):
    results = await check_availability(req.isbns, req.library_name)
    return results

@app.get("/health")
def health():
    return {"status": "ok"}
