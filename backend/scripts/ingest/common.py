"""Shared helpers for the ingestion scripts (architecture §10.3).

Every script is idempotent: it replaces what an earlier run of itself wrote, inside one
transaction, and tags rows with a dataset version derived from the input files' contents.
"""

import asyncio
import hashlib
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import asyncpg
import yaml

BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND_DIR / "data"
RAW_DIR = DATA_DIR / "raw"

IFCT_CSV = RAW_DIR / "ifct2017" / "compositions.csv"
USDA_DIR = RAW_DIR / "usda"
USDA_FOODS_YAML = DATA_DIR / "usda_foods.yaml"
PORTIONS_YAML = DATA_DIR / "portions.yaml"
SYNONYMS_YAML = DATA_DIR / "synonyms.yaml"
SAFETY_RULES_YAML = DATA_DIR / "safety_rules.yaml"
GUIDANCE_DIR = DATA_DIR / "guidance"


class IngestError(RuntimeError):
    """Bad or missing input data. Ingestion stops rather than loading partial data."""


def dataset_version(prefix: str, *paths: Path) -> str:
    """ "IFCT2017-1a2b3c4d": the prefix plus a hash of the input files, so reruns are traceable."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return f"{prefix}-{digest.hexdigest()[:8]}"


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


async def connect() -> asyncpg.Connection:
    sys.path.insert(0, str(BACKEND_DIR))
    from app.config import get_settings

    dsn = get_settings().database_url
    if not dsn:
        sys.exit("DATABASE_URL is not set")
    return await asyncpg.connect(dsn, statement_cache_size=0)


def run(main: Callable[[asyncpg.Connection], Awaitable[str]]) -> None:
    """Run one loader against DATABASE_URL and print its summary."""

    async def go() -> None:
        conn = await connect()
        try:
            print(await main(conn))
        finally:
            await conn.close()

    try:
        asyncio.run(go())
    except IngestError as exc:
        sys.exit(f"error: {exc}")
