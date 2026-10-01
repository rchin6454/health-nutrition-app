import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.api import chat, conversations, schema
from app.config import get_settings
from app.knowledge import embeddings
from app.llm.client import budget_for
from app.store import conversations as store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    dsn = get_settings().database_url
    if dsn:
        try:
            await db.open_pool(dsn)
        except Exception:
            # Keep serving: /api/health reports the problem, and chat requests return
            # recorded error responses instead of crashing.
            logger.exception("could not open the database pool")
        else:
            try:
                await seed_llm_budgets()
            except Exception:
                # The budgets still work, but today's earlier usage is not counted.
                logger.exception("could not seed Groq budgets from stored usage")
    else:
        logger.warning("DATABASE_URL is not set; storage is unavailable")
    await load_embedding_model()
    yield
    await db.close_pool()


async def load_embedding_model() -> None:
    """Load the passage-retrieval model once. If it fails, keep serving: /api/health reports it,
    and each chat turn records `retrieval_failed` and answers without passages."""
    settings = get_settings()
    try:
        await asyncio.to_thread(
            embeddings.load, settings.embedding_model, settings.embedding_cache_dir
        )
    except Exception:
        logger.exception("could not load the embedding model %s", settings.embedding_model)
    else:
        logger.info("embedding model %s loaded", settings.embedding_model)


async def seed_llm_budgets() -> None:
    """Count the last 24 hours of stored Groq usage against each model's daily budget."""
    settings = get_settings()
    model_by_call = {"understanding": settings.model_understanding, "answer": settings.model_answer}
    past: dict[str, list[tuple[float, int]]] = {}
    for age_s, usage in await store.recent_usage(hours=24):
        # Phase 2 stores {"understanding": {...}, "answer": {...}}; Phase 1 stored the answer
        # call's usage directly.
        per_call = usage if set(usage) <= set(model_by_call) else {"answer": usage}
        for call, call_usage in per_call.items():
            if isinstance(call_usage, dict) and call_usage.get("total_tokens"):
                past.setdefault(model_by_call[call], []).append(
                    (age_s, int(call_usage["total_tokens"]))
                )
    for model, calls in past.items():
        budget_for(model).seed(calls)
        logger.info("groq %s budget seeded from history: %s", model, budget_for(model).usage())


app = FastAPI(title="Food, Nutrition & Food Safety Chatbot API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins_list,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(schema.router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    database_ok = await db.ping()
    embedding_status = embeddings.status()
    return {
        "status": "ok" if database_ok and embedding_status == "ok" else "degraded",
        "database": "ok" if database_ok else "unavailable",
        "embeddings": embedding_status,
    }
