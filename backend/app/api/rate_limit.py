"""Per-IP rate limiting of POST /api/chat (slowapi, in-memory: one Railway replica).

This protects the shared Groq budget from one client. It is separate from the per-model Groq
budget in `app.llm.rate_limit`, which protects Groq's own limits across all clients.
"""

import logging
import math
import time
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app.config import get_settings
from app.failures import record_failure

logger = logging.getLogger(__name__)

TOO_MANY_REQUESTS = "Too many requests. Please wait a moment before asking again."


def client_ip(request: Request) -> str:
    """The caller's IP. Behind `trusted_proxy_hops` proxies (Railway's edge: 1), it is the
    address the outermost trusted proxy appended to X-Forwarded-For; entries further left were
    written by the client and could be spoofed."""
    hops = get_settings().trusted_proxy_hops
    forwarded = request.headers.get("x-forwarded-for")
    if hops > 0 and forwarded:
        addresses = [a.strip() for a in forwarded.split(",") if a.strip()]
        if addresses:
            return addresses[max(len(addresses) - hops, 0)]
    return request.client.host if request.client else "unknown"


def chat_limit() -> str:
    return get_settings().rate_limit_chat


limiter = Limiter(key_func=client_ip)


def _retry_after_s(request: Request) -> int:
    current = getattr(request.state, "view_rate_limit", None)
    if current is None:
        return 60
    try:
        reset_at, _remaining = limiter.limiter.get_window_stats(current[0], *current[1])
    except Exception:
        return 60
    return max(1, math.ceil(reset_at - time.time()))


async def rate_limit_exceeded(request: Request, exc: Exception) -> JSONResponse:
    """HTTP 429 with Retry-After. Recorded as a warning: no model call was made."""
    assert isinstance(exc, RateLimitExceeded)  # noqa: S101 - registered for this type only
    retry_after = _retry_after_s(request)
    await record_failure(
        request_id=str(uuid4()),
        conversation_id=None,
        stage="input_gate",
        failure_type="ip_rate_limited",
        severity="warning",
        error_detail=f"{request.url.path}: limit {exc.detail}; retry after {retry_after}s",
    )
    return JSONResponse(
        {"detail": TOO_MANY_REQUESTS, "retry_after_s": retry_after},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )
