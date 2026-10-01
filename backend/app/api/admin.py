"""GET /api/admin/failures: the `failures` table for review (architecture §8).

Requires the X-Admin-Token header to equal ADMIN_TOKEN; while ADMIN_TOKEN is unset the endpoint
answers 404, as if it did not exist.
"""

import hmac
import logging
from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app import db
from app.config import get_settings
from app.failures import Severity, Stage, record_failure, storage_failure_type
from app.schemas.admin import FailureOut, FailuresPage

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_LIMIT = 50
MAX_LIMIT = 500


def require_admin(x_admin_token: Annotated[str | None, Header()] = None) -> None:
    expected = get_settings().admin_token
    if not expected:
        raise HTTPException(404, "Not Found")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(401, "Invalid or missing X-Admin-Token header")


@router.get("/api/admin/failures", dependencies=[Depends(require_admin)])
async def list_failures(
    failure_type: str | None = None,
    stage: Stage | None = None,
    severity: Severity | None = None,
    since: Annotated[datetime | None, Query(description="created_at >= since (ISO 8601)")] = None,
    until: Annotated[datetime | None, Query(description="created_at < until (ISO 8601)")] = None,
    before_id: Annotated[int | None, Query(description="next page: the last id seen")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> FailuresPage:
    """Newest first. Every filter is optional; pass `before_id` to read the next page."""
    try:
        rows = await db.get_pool().fetch(
            """
            SELECT id, created_at, request_id, conversation_id, stage, failure_type, severity,
                   model, prompt_version, user_message, raw_output, error_detail, latency_ms
            FROM failures
            WHERE ($1::text IS NULL OR failure_type = $1)
              AND ($2::text IS NULL OR stage = $2)
              AND ($3::text IS NULL OR severity = $3)
              AND ($4::timestamptz IS NULL OR created_at >= $4)
              AND ($5::timestamptz IS NULL OR created_at < $5)
              AND ($6::bigint IS NULL OR id < $6)
            ORDER BY id DESC
            LIMIT $7
            """,
            failure_type,
            stage,
            severity,
            since,
            until,
            before_id,
            limit,
        )
    except Exception as exc:
        request_id = str(uuid4())
        logger.exception("reading failures failed (request_id=%s)", request_id)
        await record_failure(
            request_id=request_id,
            conversation_id=None,
            stage="storage",
            failure_type=storage_failure_type(exc),
            error_detail=repr(exc),
        )
        raise HTTPException(503, f"Failures storage unavailable (reference: {request_id})") from exc

    failures = [
        FailureOut(
            **{
                **dict(r),
                "request_id": str(r["request_id"]),
                "conversation_id": str(r["conversation_id"]) if r["conversation_id"] else None,
            }
        )
        for r in rows
    ]
    next_before_id = failures[-1].id if len(failures) == limit else None
    return FailuresPage(failures=failures, next_before_id=next_before_id)
