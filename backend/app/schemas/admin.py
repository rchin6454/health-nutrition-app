"""Response models for the admin endpoints."""

from datetime import datetime

from app.schemas.answer import Strict


class FailureOut(Strict):
    """One `failures` row (architecture §8.2), exactly as stored."""

    id: int
    created_at: datetime
    request_id: str
    conversation_id: str | None
    stage: str
    failure_type: str
    severity: str
    model: str | None
    prompt_version: str | None
    user_message: str | None
    raw_output: str | None
    error_detail: str | None
    latency_ms: int | None


class FailuresPage(Strict):
    failures: list[FailureOut]  # newest first
    next_before_id: int | None  # pass as `before_id` for the next page; null on the last page
