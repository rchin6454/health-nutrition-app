"""Shared fixtures: a real Postgres for storage tests and a stubbed Groq client.

Postgres comes from TEST_DATABASE_URL (CI) or, if unset, an embedded server via `pgserver`.
Tests never talk to the real Groq API or the Supabase database.
"""

import asyncio
import logging
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import asyncpg
import httpx
import pytest

from app import db
from app.llm import client as llm_client
from app.main import app
from scripts.migrate import apply_migrations


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
    """A fresh pool on empty tables, installed as the app's pool."""
    pool = await db.open_pool(migrated_dsn)
    await pool.execute("TRUNCATE conversations, messages, failures RESTART IDENTITY CASCADE")
    yield pool
    await db.close_pool()


@pytest.fixture
async def api() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@dataclass
class FakeGroq:
    """Stands in for `AsyncGroq`. Set `outcome` to a raw content string or an exception."""

    outcome: str | Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def chat(self) -> Any:
        return SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        message = SimpleNamespace(content=self.outcome)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)], model=kwargs["model"], usage=None
        )


@pytest.fixture
def fake_groq(monkeypatch: pytest.MonkeyPatch) -> FakeGroq:
    fake = FakeGroq()
    monkeypatch.setattr(llm_client, "_get_client", lambda: fake)
    return fake
