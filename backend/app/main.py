import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.api import chat, conversations, schema
from app.config import get_settings

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
        logger.warning("DATABASE_URL is not set; storage is unavailable")
    yield
    await db.close_pool()


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
    return {
        "status": "ok" if database_ok else "degraded",
        "database": "ok" if database_ok else "unavailable",
    }
