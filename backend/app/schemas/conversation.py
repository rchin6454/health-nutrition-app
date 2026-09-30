"""Request and history models for the chat and conversation endpoints."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.schemas.answer import ChatResponse, Strict

MAX_MESSAGE_CHARS = 1000


class ChatRequest(Strict):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    conversation_id: UUID
    message: Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE_CHARS)]


class UserMessageOut(Strict):
    role: Literal["user"]
    request_id: str
    created_at: datetime
    text: str


class AssistantMessageOut(Strict):
    role: Literal["assistant"]
    request_id: str
    created_at: datetime
    response: ChatResponse  # exactly what POST /api/chat returned


class ConversationOut(Strict):
    conversation_id: str
    messages: list[Annotated[UserMessageOut | AssistantMessageOut, Field(discriminator="role")]]
