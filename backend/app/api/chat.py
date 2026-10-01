"""POST /api/chat: always HTTP 200 with a validated `ChatResponse` (malformed input → 422)."""

import logging
import time
from uuid import uuid4

from fastapi import APIRouter, Request

from app.api.rate_limit import chat_limit, limiter
from app.failures import Stage, record_failure, storage_failure_type
from app.logging_config import bind_request
from app.pipeline.orchestrator import run_turn
from app.pipeline.prompt_builder import HISTORY_TURNS
from app.prompts import PROMPT_VERSION
from app.responses import build_error_response
from app.schemas.answer import ChatResponse
from app.schemas.conversation import ChatRequest
from app.store import conversations as store

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/chat")
@limiter.limit(chat_limit)
async def chat(request: Request, req: ChatRequest) -> ChatResponse:
    start = time.perf_counter()
    request_id = str(uuid4())
    conversation_id = str(req.conversation_id)
    message = req.message
    bind_request(request_id, conversation_id)

    async def fail(stage: Stage, failure_type: str, exc: Exception) -> ChatResponse:
        logger.exception("chat %s failed", stage)
        await record_failure(
            request_id=request_id,
            conversation_id=conversation_id,
            stage=stage,
            failure_type=failure_type,
            prompt_version=PROMPT_VERSION,
            user_message=message,
            error_detail=repr(exc),
        )
        return build_error_response(request_id=request_id, conversation_id=conversation_id)

    try:
        await store.upsert_conversation(conversation_id, title=message)
        history = await store.load_recent(conversation_id, HISTORY_TURNS)
        user_message_id = await store.add_message(
            conversation_id=conversation_id,
            request_id=request_id,
            role="user",
            content={"text": message},
        )
    except Exception as exc:
        return await fail("storage", storage_failure_type(exc), exc)

    try:
        turn = await run_turn(
            request_id=request_id,
            conversation_id=conversation_id,
            message=message,
            history=history,
        )
    except Exception as exc:  # a bug: run_turn records its own expected failures
        return await fail("generation", "unhandled_exception", exc)

    try:
        if turn.analysis is not None:
            await store.set_analysis(user_message_id, turn.analysis.model_dump(mode="json"))
        await store.add_message(
            conversation_id=conversation_id,
            request_id=request_id,
            role="assistant",
            content=turn.response.model_dump(mode="json"),
            model=turn.model,
            prompt_version=turn.prompt_version,
            latency_ms=turn.latency_ms,
            usage=turn.usage,
            context_snapshot=turn.context.model_dump(mode="json") if turn.context else None,
        )
    except Exception as exc:
        # Returning an answer that reloads can't restore would desync UI and database.
        return await fail("storage", storage_failure_type(exc), exc)

    logger.info(
        "chat turn completed",
        extra={
            "fields": {
                "event": "chat_turn",
                "answer_type": turn.response.answer_type,
                "category": turn.response.category,
                "prompt_version": turn.prompt_version,
                "latency_ms": int((time.perf_counter() - start) * 1000),
                "pipeline_ms": turn.latency_ms,
                "stage_ms": turn.stage_ms,
                "tokens": {
                    call: u.get("total_tokens")
                    for call, u in (turn.usage or {}).items()
                    if isinstance(u, dict)
                },
            }
        },
    )
    return turn.response
