"""Shared fixtures: a real Postgres for storage tests and a stubbed Groq client.

Postgres comes from TEST_DATABASE_URL (CI) or, if unset, an embedded server via `pgserver`.
Tests never talk to the real Groq API or the Supabase database.
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import tempfile
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg
import httpx
import pytest

from app import db
from app.knowledge import embeddings, entity_resolver
from app.llm import client as llm_client
from app.main import app
from scripts.ingest.build_synonyms import build_synonyms
from scripts.ingest.chunk_and_embed import chunk_and_embed
from scripts.ingest.load_ifct import load_ifct
from scripts.ingest.load_portions import load_portions
from scripts.ingest.load_safety_rules import load_safety_rules
from scripts.ingest.load_usda import load_usda
from scripts.migrate import apply_migrations

KNOWLEDGE_FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"
KNOWLEDGE_TABLES = (
    "foods, food_synonyms, nutrients, portion_weights, safety_rules, safety_food_aliases, "
    "doc_chunks"
)


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if dsn:
        yield dsn
        return
    try:
        import pgserver
    except ImportError:
        pytest.skip("no TEST_DATABASE_URL and pgserver is not installed")
    logging.getLogger("pgserver").setLevel(logging.WARNING)  # noisy atexit logging
    with tempfile.TemporaryDirectory(prefix="pgtest-") as pgdata:
        server = pgserver.get_server(pgdata, cleanup_mode="stop")
        yield server.get_uri()
        server.cleanup()


@pytest.fixture(scope="session")
def migrated_dsn(pg_dsn: str) -> str:
    async def migrate() -> None:
        conn = await asyncpg.connect(pg_dsn)
        try:
            await apply_migrations(conn)
        finally:
            await conn.close()

    asyncio.run(migrate())
    return pg_dsn


@pytest.fixture
async def pool(migrated_dsn: str) -> AsyncIterator[asyncpg.Pool]:
    """A fresh pool on empty tables (knowledge tables too), installed as the app's pool."""
    pool = await db.open_pool(migrated_dsn)
    await pool.execute(
        f"TRUNCATE conversations, messages, failures, {KNOWLEDGE_TABLES} RESTART IDENTITY CASCADE"
    )
    entity_resolver.clear_cache()
    yield pool
    entity_resolver.clear_cache()
    await db.close_pool()


class HashingEmbedder:
    """A deterministic stand-in for the fastembed model: a bag of words hashed into 384 dims
    (signed feature hashing).

    Texts that share words score higher, which is enough to test retrieval without downloading
    the real model.
    """

    # Sharing a word or two with a ~70-word passage scores about 0.05-0.15; no relative cut.
    min_score = 0.15
    score_margin = 1.0
    _STOPWORDS = frozenset(
        "a an and are be by can do does for from how i in is it its long of on or the to was "
        "what when with".split()
    )

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * embeddings.EMBEDDING_DIM
        for word in re.findall(r"[a-z]+", text.lower()):
            if word in self._STOPWORDS:
                continue
            word = word[:-1] if word.endswith("s") and len(word) > 3 else word
            digest = int(hashlib.blake2b(word.encode(), digest_size=16).hexdigest(), 16)
            # Signed hashing: colliding words cancel out instead of looking similar.
            vector[digest % len(vector)] += 1.0 if digest & (1 << 64) else -1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]


@pytest.fixture(autouse=True)
def fake_embedder() -> Iterator[HashingEmbedder]:
    """Every test retrieves passages with the hashing embedder, never the real model."""
    embedder = HashingEmbedder()
    embeddings.set_embedder(embedder)
    yield embedder
    embeddings.set_embedder(None)


async def load_knowledge_fixtures(conn: asyncpg.Connection) -> None:
    """Run the real loaders on the small fixture datasets and the real curated data files."""
    ifct_csv = KNOWLEDGE_FIXTURES / "ifct_compositions.csv"
    await load_ifct(conn, ifct_csv)
    await load_usda(conn, KNOWLEDGE_FIXTURES / "usda", KNOWLEDGE_FIXTURES / "usda_foods.yaml")
    await load_portions(conn)
    await build_synonyms(conn, ifct_csv)
    await load_safety_rules(conn)
    await chunk_and_embed(conn, embedder=HashingEmbedder())


@pytest.fixture
async def knowledge(pool: asyncpg.Pool) -> asyncpg.Pool:
    """`pool`, with the knowledge tables loaded from tests/fixtures/knowledge/."""
    async with pool.acquire() as conn:
        await load_knowledge_fixtures(conn)
    return pool


@pytest.fixture
async def api() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def analysis_json(**overrides: Any) -> str:
    """A valid `QuestionAnalysis` as raw model output: an in-scope nutrition question."""
    analysis: dict[str, Any] = {
        "intent_summary": "Compare the healthiness of brown and white rice.",
        "category": "nutrition",
        "question_type": "comparison",
        "entities": {
            "foods": ["brown rice", "white rice"],
            "nutrients": [],
            "quantities": [],
            "storage": None,
            "cooking_methods": [],
        },
        "user_context": [],
        "needs_clarification": False,
        "clarifying_question": None,
        "risk_flags": [],
    }
    analysis.update(overrides)
    return json.dumps(analysis)


@dataclass
class FakeGroq:
    """Stands in for `AsyncGroq`. Each outcome is a raw content string or an exception.

    `analysis` answers the understanding call; `outcome` answers every other call. Each
    successful call reports `total_tokens` of usage and the rate-limit `headers`.
    """

    outcome: str | Exception | None = None
    analysis: str | Exception | None = field(default_factory=analysis_json)
    total_tokens: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def schema_names(self) -> list[str]:
        return [c["response_format"]["json_schema"]["name"] for c in self.calls]

    @property
    def chat(self) -> Any:
        return SimpleNamespace(
            completions=SimpleNamespace(
                create=self._create,
                with_raw_response=SimpleNamespace(create=self._create_raw),
            )
        )

    async def _create_raw(self, **kwargs: Any) -> Any:
        parsed = await self._create(**kwargs)

        async def parse() -> Any:
            return parsed

        return SimpleNamespace(headers=self.headers, parse=parse)

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        is_understanding = kwargs["response_format"]["json_schema"]["name"] == "question_analysis"
        outcome = self.analysis if is_understanding else self.outcome
        if isinstance(outcome, Exception):
            raise outcome
        message = SimpleNamespace(content=outcome)
        usage = None
        if self.total_tokens is not None:
            counts = {"total_tokens": self.total_tokens}
            usage = SimpleNamespace(**counts, model_dump=lambda: counts)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)], model=kwargs["model"], usage=usage
        )


@pytest.fixture(autouse=True)
def fresh_llm_budgets() -> Iterator[None]:
    """Every test starts with unused Groq budgets."""
    llm_client.reset_budgets()
    yield
    llm_client.reset_budgets()


@pytest.fixture
def fake_groq(monkeypatch: pytest.MonkeyPatch) -> FakeGroq:
    fake = FakeGroq()
    monkeypatch.setattr(llm_client, "_get_client", lambda: fake)
    return fake
