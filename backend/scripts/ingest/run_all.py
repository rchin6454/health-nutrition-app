"""Run every knowledge loader in order against DATABASE_URL (Phase 3 nutrition, Phase 4 safety).

Usage (from backend/):
    uv run python -m scripts.ingest.fetch_raw   # once: download the raw datasets
    uv run python -m scripts.ingest.run_all

Restart the backend afterwards: the food-name index is cached per process. The last step
embeds the guidance documents, which downloads the embedding model on its first run.
"""

import asyncpg

from scripts.ingest.build_synonyms import build_synonyms
from scripts.ingest.chunk_and_embed import chunk_and_embed
from scripts.ingest.common import run
from scripts.ingest.load_ifct import load_ifct
from scripts.ingest.load_portions import load_portions
from scripts.ingest.load_safety_rules import load_safety_rules
from scripts.ingest.load_usda import load_usda


async def run_all(conn: asyncpg.Connection) -> str:
    return "\n".join(
        [
            await load_ifct(conn),
            await load_usda(conn),
            await load_portions(conn),
            await build_synonyms(conn),
            await load_safety_rules(conn),
            await chunk_and_embed(conn),
        ]
    )


if __name__ == "__main__":
    run(run_all)
