"""Read-only launch checks on stored data (implementation plan, Phase 6 checklist).

- R4: no stored claim has a non-null `source`.
- R2: every stored assistant message validates as a `ChatResponse`.
- R7/R10: row counts for conversations, messages and failures.

Usage (from backend/):  uv run python scripts/check_stored.py
Reads DATABASE_URL from the environment or backend/.env. The session is read-only.
Exits non-zero if R2 or R4 fails.
"""

import asyncio
import json
import sys
from pathlib import Path

import asyncpg

BACKEND_DIR = Path(__file__).resolve().parent.parent


async def main() -> int:
    sys.path.insert(0, str(BACKEND_DIR))
    from app.config import get_settings
    from app.schemas.answer import ChatResponse

    conn = await asyncpg.connect(get_settings().database_url, statement_cache_size=0)
    try:
        await conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        non_null = await conn.fetchval(
            "SELECT count(*) FROM messages, jsonb_array_elements(content->'claims') c "
            "WHERE c->'source' <> 'null'::jsonb"
        )
        claims = await conn.fetchval(
            "SELECT count(*) FROM messages, jsonb_array_elements(content->'claims') c"
        )
        print(f"R4  claims with a non-null source: {non_null} (of {claims})")

        rows = await conn.fetch("SELECT id, content FROM messages WHERE role = 'assistant'")
        invalid = []
        for row in rows:
            content = row["content"]
            try:
                ChatResponse.model_validate(
                    json.loads(content) if isinstance(content, str) else content
                )
            except ValueError as e:
                invalid.append((row["id"], str(e).splitlines()[0]))
        print(f"R2  stored assistant messages valid: {len(rows) - len(invalid)}/{len(rows)}")
        for message_id, error in invalid[:10]:
            print(f"      invalid message {message_id}: {error}")

        conversations = await conn.fetchval("SELECT count(*) FROM conversations")
        by_role = dict(await conn.fetch("SELECT role, count(*) FROM messages GROUP BY role"))
        print(f"R10 conversations: {conversations}; messages by role: {by_role}")
        print("R7  failures by stage / type / severity:")
        for row in await conn.fetch(
            "SELECT stage, failure_type, severity, count(*) AS n, max(created_at) AS last "
            "FROM failures GROUP BY 1, 2, 3 ORDER BY n DESC"
        ):
            print(
                f"      {row['stage']:<14} {row['failure_type']:<28} {row['severity']:<8} "
                f"{row['n']:>5}  last {row['last']:%Y-%m-%d %H:%M}"
            )
    finally:
        await conn.close()
    return 1 if non_null or invalid else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
