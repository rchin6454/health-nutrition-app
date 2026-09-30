"""Model call 2: answer generation (Phase 1: the only model call)."""

from app.config import get_settings
from app.llm.client import ChatMessage, LLMResult, structured_call
from app.llm.strict_schema import to_groq_strict
from app.prompts import ANSWER_SYSTEM_PROMPT
from app.schemas.answer import LLMAnswer
from app.store.conversations import StoredMessage

HISTORY_TURNS = 6

LLM_ANSWER_SCHEMA = to_groq_strict(LLMAnswer)


def build_messages(question: str, history: list[StoredMessage]) -> list[ChatMessage]:
    """System prompt + recent turns (answer text only) + the tagged user question."""
    messages: list[ChatMessage] = [{"role": "system", "content": ANSWER_SYSTEM_PROMPT}]
    for msg in history:
        if msg.role == "user":
            messages.append({"role": "user", "content": str(msg.content.get("text", ""))})
        elif msg.content.get("answer_type") != "error":
            messages.append({"role": "assistant", "content": str(msg.content.get("answer", ""))})
    messages.append({"role": "user", "content": f"<user_question>\n{question}\n</user_question>"})
    return messages


async def generate_answer(question: str, history: list[StoredMessage]) -> LLMResult:
    """Raises `LLMCallError` on any Groq failure."""
    return await structured_call(
        model=get_settings().model_answer,
        messages=build_messages(question, history),
        schema_name="llm_answer",
        schema=LLM_ANSWER_SCHEMA,
    )
