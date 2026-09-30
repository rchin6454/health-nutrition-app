"""GET /api/schema: the `ChatResponse` JSON Schema, used to generate frontend types."""

from typing import Any

from fastapi import APIRouter

from app.schemas.answer import ChatResponse

router = APIRouter()


@router.get("/api/schema")
async def chat_response_schema() -> dict[str, Any]:
    return ChatResponse.model_json_schema()
