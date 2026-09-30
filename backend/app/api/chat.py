"""POST /api/chat: always HTTP 200 with a validated `ChatResponse` (malformed input → 422)."""

import logging
from uuid import uuid4

from fastapi import APIRouter

from app.failures import Stage, record_failure
from app.pipeline.answer import HISTORY_TURNS
from app.pipeline.orchestrator import run_turn
from app.prompts import PROMPT_VERSION
from app.responses import build_error_response
from app.schemas.answer import ChatResponse
from app.schemas.conversation import ChatRequest
from app.store import conversations as store

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    request_id = str(uuid4())
    conversation_id = str(req.conversation_id)
    message = req.message

    async def fail(stage: Stage, failure_type: str, exc: Exception) -> ChatResponse:
        logger.exception("chat %s failed (request_id=%s)", stage, request_id)
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
        await store.add_message(
            conversation_id=conversation_id,
            request_id=request_id,
            role="user",
            content={"text": message},
        )
    except Exception as exc:
        return await fail("storage", "storage_error", exc)

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
        await store.add_message(
            conversation_id=conversation_id,
            request_id=request_id,
            role="assistant",
            content=turn.response.model_dump(mode="json"),
            model=turn.model,
            prompt_version=turn.prompt_version,
            latency_ms=turn.latency_ms,
            usage=turn.usage,
        )
    except Exception as exc:
        # Returning an answer that reloads can't restore would desync UI and database.
        return await fail("storage", "storage_error", exc)

    return turn.response
