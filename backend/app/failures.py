"""Failure recording (architecture §8): every failure becomes a `failures` row, never a patch."""

import json
import logging
import sys
from typing import Literal
from uuid import UUID

import asyncpg

from app import db

logger = logging.getLogger(__name__)

Stage = Literal[
    "input_gate",
    "understanding",
    "retrieval",
    "generation",
    "validation",
    "output_gate",
    "upstream_api",
    "storage",
]
Severity = Literal["error", "warning", "scope_block"]


def storage_failure_type(exc: BaseException) -> str:
    """Database timeouts are recorded as their own type: the client-side `db.QUERY_TIMEOUT_S`
    (TimeoutError) and a server-side statement timeout (QueryCanceledError)."""
    timed_out = isinstance(exc, TimeoutError | asyncpg.QueryCanceledError)
    return "db_timeout" if timed_out else "storage_error"


_INSERT = """
INSERT INTO failures (
    request_id, conversation_id, stage, failure_type, severity, model, prompt_version,
    user_message, raw_output, error_detail, latency_ms
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
"""


async def record_failure(
    *,
    request_id: str,
    conversation_id: str | None,
    stage: Stage,
    failure_type: str,
    severity: Severity = "error",
    model: str | None = None,
    prompt_version: str | None = None,
    user_message: str | None = None,
    raw_output: str | None = None,
    error_detail: str | None = None,
    latency_ms: int | None = None,
) -> None:
    """Write one `failures` row. Never raises: if the write fails, the payload goes to stderr."""
    payload = {
        "request_id": request_id,
        "conversation_id": conversation_id,
        "stage": stage,
        "failure_type": failure_type,
        "severity": severity,
        "model": model,
        "prompt_version": prompt_version,
        "user_message": user_message,
        "raw_output": raw_output,
        "error_detail": error_detail,
        "latency_ms": latency_ms,
    }
    logger.warning("failure recorded: %s/%s (request_id=%s)", stage, failure_type, request_id)
    try:
        await db.get_pool().execute(
            _INSERT,
            UUID(request_id),
            UUID(conversation_id) if conversation_id else None,
            stage,
            failure_type,
            severity,
            model,
            prompt_version,
            user_message,
            raw_output,
            error_detail,
            latency_ms,
        )
    except Exception as exc:
        try:
            print(
                "FAILURE_RECORD_WRITE_FAILED "
                + json.dumps({"write_error": repr(exc), "failure": payload}, default=str),
                file=sys.stderr,
                flush=True,
            )
        except Exception:  # noqa: S110 - stderr itself is broken; nothing left to report to
            pass
