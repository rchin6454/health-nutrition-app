"""After a load test: check that every `error` response has a `failures` row (R7).

Usage (from backend/):  DATABASE_URL=postgresql://... uv run python -m tests.load.check_failures
Reads the request ids saved by locustfile.py (LOAD_RESULTS_FILE). Exit code 1 if any is missing.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

from app.config import get_settings

RESULTS_FILE = Path(os.environ.get("LOAD_RESULTS_FILE", "tests/load/results/last_run.json"))


async def main(run: dict[str, Any]) -> int:
    request_ids = [UUID(r) for r in run["error_request_ids"]]
    dsn = get_settings().database_url
    if not dsn:
        sys.exit("DATABASE_URL is not set")
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        rows = await conn.fetch(
            """
            SELECT request_id, stage, failure_type FROM failures
            WHERE request_id = ANY($1::uuid[]) AND severity = 'error'
            """,
            request_ids,
        )
    finally:
        await conn.close()
    recorded = {r["request_id"] for r in rows}
    missing = [str(r) for r in request_ids if r not in recorded]
    types: dict[str, int] = {}
    for r in rows:
        key = f"{r['stage']}/{r['failure_type']}"
        types[key] = types.get(key, 0) + 1
    print(f"{len(request_ids)} error responses; {len(recorded)} have a failures row {types}")
    if missing:
        print(f"MISSING failures rows for {len(missing)} request(s): {missing[:10]}")
        return 1
    print("every error response has a failures row")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(json.loads(RESULTS_FILE.read_text(encoding="utf-8")))))
