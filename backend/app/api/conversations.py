"""GET / DELETE /api/conversations/{id}."""

import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Response

from app.failures import record_failure
from app.schemas.answer import ChatResponse
from app.schemas.conversation import AssistantMessageOut, ConversationOut, UserMessageOut
from app.store import conversations as store
from app.store.conversations import StoredMessage

logger = logging.getLogger(__name__)

router = APIRouter()


async def _storage_unavailable(conversation_id: str, exc: Exception) -> HTTPException:
    request_id = str(uuid4())
    logger.exception("conversation storage failed (request_id=%s)", request_id)
    await record_failure(
        request_id=request_id,
        conversation_id=conversation_id,
        stage="storage",
        failure_type="storage_error",
        error_detail=repr(exc),
    )
    return HTTPException(503, f"Conversation storage unavailable (reference: {request_id})")


def _to_out(msg: StoredMessage) -> UserMessageOut | AssistantMessageOut:
    if msg.role == "user":
        return UserMessageOut(
            role="user",
            request_id=msg.request_id,
            created_at=msg.created_at,
            text=str(msg.content["text"]),
        )
    return AssistantMessageOut(
        role="assistant",
        request_id=msg.request_id,
        created_at=msg.created_at,
        response=ChatResponse.model_validate(msg.content),
    )


@router.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: UUID) -> ConversationOut:
    try:
        messages = await store.load_all(str(conversation_id))
    except Exception as exc:
        raise await _storage_unavailable(str(conversation_id), exc) from exc
    if messages is None:
        raise HTTPException(404, "Conversation not found")
    return ConversationOut(
        conversation_id=str(conversation_id), messages=[_to_out(m) for m in messages]
    )


@router.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: UUID) -> Response:
    try:
        deleted = await store.delete(str(conversation_id))
    except Exception as exc:
        raise await _storage_unavailable(str(conversation_id), exc) from exc
    if not deleted:
        raise HTTPException(404, "Conversation not found")
    return Response(status_code=204)
