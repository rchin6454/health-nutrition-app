"""Conversation storage (architecture §8.3)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import asyncpg

from app import db

TITLE_MAX_CHARS = 80

Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class StoredMessage:
    id: int
    created_at: datetime
    request_id: str
    role: Role
    content: dict[str, Any]


def _to_message(row: asyncpg.Record) -> StoredMessage:
    return StoredMessage(
        id=row["id"],
        created_at=row["created_at"],
        request_id=str(row["request_id"]),
        role=row["role"],
        content=row["content"],
    )


async def upsert_conversation(conversation_id: str, title: str) -> None:
    """Create the conversation on its first message; afterwards only bump `updated_at`."""
    await db.get_pool().execute(
        """
        INSERT INTO conversations (id, title) VALUES ($1, $2)
        ON CONFLICT (id) DO UPDATE SET updated_at = now()
        """,
        UUID(conversation_id),
        title[:TITLE_MAX_CHARS],
    )


async def add_message(
    *,
    conversation_id: str,
    request_id: str,
    role: Role,
    content: dict[str, Any],
    model: str | None = None,
    prompt_version: str | None = None,
    latency_ms: int | None = None,
    usage: dict[str, Any] | None = None,
) -> None:
    await db.get_pool().execute(
        """
        INSERT INTO messages (
            conversation_id, request_id, role, content, model, prompt_version, latency_ms, usage
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        UUID(conversation_id),
        UUID(request_id),
        role,
        content,
        model,
        prompt_version,
        latency_ms,
        usage,
    )


async def load_recent(conversation_id: str, n: int) -> list[StoredMessage]:
    """The last `n` messages, oldest first."""
    rows = await db.get_pool().fetch(
        """
        SELECT * FROM (
            SELECT id, created_at, request_id, role, content FROM messages
            WHERE conversation_id = $1 ORDER BY id DESC LIMIT $2
        ) recent ORDER BY id
        """,
        UUID(conversation_id),
        n,
    )
    return [_to_message(r) for r in rows]


async def load_all(conversation_id: str) -> list[StoredMessage] | None:
    """All messages oldest first, or `None` if the conversation does not exist."""
    pool = db.get_pool()
    exists = await pool.fetchval("SELECT 1 FROM conversations WHERE id = $1", UUID(conversation_id))
    if not exists:
        return None
    rows = await pool.fetch(
        """
        SELECT id, created_at, request_id, role, content FROM messages
        WHERE conversation_id = $1 ORDER BY id
        """,
        UUID(conversation_id),
    )
    return [_to_message(r) for r in rows]


async def delete(conversation_id: str) -> bool:
    """Delete a conversation and (via ON DELETE CASCADE) its messages. True if it existed."""
    result = await db.get_pool().execute(
        "DELETE FROM conversations WHERE id = $1", UUID(conversation_id)
    )
    return result != "DELETE 0"
