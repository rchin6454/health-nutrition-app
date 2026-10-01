"""Request and history models for the chat and conversation endpoints."""

import re
import unicodedata
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from app.schemas.answer import ChatResponse, Strict

MAX_MESSAGE_CHARS = 1000

# Control characters (except tab and newline), and invisible format characters that can hide
# text from a reader: zero-width spaces/joiners, bidirectional overrides and isolates, BOM.
_UNSAFE_CHARS = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]"
)


def sanitize_message(text: str) -> str:
    """NFC-normalize, turn CRLF/CR into newlines and drop control and invisible characters."""
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    return _UNSAFE_CHARS.sub("", text)


class ChatRequest(Strict):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    conversation_id: UUID
    # Sanitized first, then stripped and length-checked (over-long messages get HTTP 422).
    message: Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE_CHARS)]

    @field_validator("message", mode="before")
    @classmethod
    def _sanitize(cls, value: Any) -> Any:
        return sanitize_message(value) if isinstance(value, str) else value


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
