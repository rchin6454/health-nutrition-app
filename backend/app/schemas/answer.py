"""Response schema (architecture §6.2).

`LLMAnswer` is what the model may return; `ChatResponse` is what the API returns.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")  # → additionalProperties: false


class Claim(Strict):
    text: str  # claim text: one short, checkable statement
    source: None  # source field: always null (R4)


class LLMAnswer(Strict):  # what the model returns (strict JSON schema)
    answer: str  # answer text shown in the message list
    claims: list[Claim]  # the claims the answer makes


AnswerType = Literal["answer", "clarification", "out_of_scope", "error"]
ResponseCategory = Literal[
    "nutrition", "food_safety", "general_food", "mixed", "out_of_scope", "none"
]


class ChatResponse(Strict):  # what POST /api/chat returns
    schema_version: Literal["1.0"]
    request_id: str
    conversation_id: str
    answer_type: AnswerType
    category: ResponseCategory  # from the understanding step, set by code
    answer: str  # from LLMAnswer, or written by code for non-answers
    claims: list[Claim]  # from LLMAnswer; empty for non-answers
    notices: list[str]  # code-owned: disclaimer, emergency notice
