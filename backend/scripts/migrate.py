"""Apply `migrations/*.sql` in order, skipping ones already recorded in `schema_migrations`.

Usage (from backend/):  uv run python scripts/migrate.py
Reads DATABASE_URL from the environment or backend/.env.
"""

import asyncio
import sys
from pathlib import Path

import asyncpg

BACKEND_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = BACKEND_DIR / "migrations"


async def apply_migrations(conn: asyncpg.Connection) -> list[str]:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name       TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    applied = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
    newly_applied: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        async with conn.transaction():
            await conn.execute(path.read_text())
            await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
        newly_applied.append(path.name)
    return newly_applied


async def main() -> None:
    sys.path.insert(0, str(BACKEND_DIR))
    from app.config import get_settings

    dsn = get_settings().database_url
    if not dsn:
        sys.exit("DATABASE_URL is not set")
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        done = await apply_migrations(conn)
    finally:
        await conn.close()
    print("applied: " + (", ".join(done) if done else "nothing (up to date)"))


if __name__ == "__main__":
    asyncio.run(main())
