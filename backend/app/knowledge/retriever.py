"""Semantic retriever over guidance passages (architecture §5.4, step 5).

Embeds `intent_summary + foods`, runs a pgvector cosine search over `doc_chunks` filtered by
category, and keeps the top 4 that score above the embedder's minimum and within its margin of
the best match. Passages are context for the model only; their `origin` stays in the audit
snapshot and never reaches the prompt or claims.
"""

import asyncio
from dataclasses import dataclass

from app import db
from app.knowledge.builder import ContextBuilder
from app.knowledge.embeddings import get_embedder, to_pgvector
from app.schemas.analysis import Category, QuestionAnalysis

TOP_K = 4

# doc_chunks categories searched for each question category.
CHUNK_CATEGORIES: dict[Category, tuple[str, ...]] = {
    "nutrition": ("nutrition",),
    "food_safety": ("food_safety",),
    "general_food": ("nutrition", "food_safety"),
    "mixed": ("nutrition", "food_safety"),
}


@dataclass(frozen=True)
class RetrievedChunk:
    text: str
    origin: str
    category: str
    score: float  # cosine similarity, 1 = identical


def retrieval_query(analysis: QuestionAnalysis) -> str:
    foods = ", ".join(analysis.entities.foods)
    return f"{analysis.intent_summary} {foods}".strip()


async def retrieve(
    query: str, categories: tuple[str, ...], top_k: int = TOP_K
) -> list[RetrievedChunk]:
    """The `top_k` closest chunks in `categories`, dropping those too far from the query or from
    the best match (the embedder's `min_score` and `score_margin`).

    Raises `EmbeddingsUnavailableError` if the model is not loaded, and database errors as is.
    """
    embedder = get_embedder()
    vector = await asyncio.to_thread(embedder.embed_query, query)
    rows = await db.get_pool().fetch(
        """
        SELECT text, origin, category, 1 - (embedding <=> $1::vector) AS score
        FROM doc_chunks
        WHERE category = ANY($2::text[])
        ORDER BY embedding <=> $1::vector
        LIMIT $3
        """,
        to_pgvector(vector),
        list(categories),
        top_k,
    )
    chunks = [
        RetrievedChunk(r["text"], r["origin"], r["category"], float(r["score"])) for r in rows
    ]
    if not chunks:
        return []
    cutoff = max(embedder.min_score, chunks[0].score - embedder.score_margin)
    return [c for c in chunks if c.score >= cutoff]


async def add_passages(builder: ContextBuilder, analysis: QuestionAnalysis) -> None:
    categories = CHUNK_CATEGORIES.get(analysis.category)
    if not categories:
        return
    for chunk in await retrieve(retrieval_query(analysis), categories):
        builder.add_passage(chunk.text, chunk.origin, chunk.score)
