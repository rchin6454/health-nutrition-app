"""Model call 2: answer generation."""

from app.config import get_settings
from app.llm.client import LLMResult, structured_call
from app.llm.strict_schema import to_groq_strict
from app.pipeline.prompt_builder import build_answer_messages
from app.schemas.analysis import QuestionAnalysis
from app.schemas.answer import LLMAnswer
from app.schemas.context import ContextBundle
from app.store.conversations import StoredMessage

LLM_ANSWER_SCHEMA = to_groq_strict(LLMAnswer)
# Hard cap on reasoning + JSON (observed 530-740). Hitting it truncates the JSON, which is
# recorded as a validation failure, never repaired.
MAX_COMPLETION_TOKENS = 2500


async def generate_answer(
    question: str,
    history: list[StoredMessage],
    analysis: QuestionAnalysis,
    context: ContextBundle | None = None,
) -> LLMResult:
    """Raises `LLMCallError` on any Groq failure or when the model's budget is exhausted."""
    settings = get_settings()
    return await structured_call(
        model=settings.model_answer,
        messages=build_answer_messages(question, history, analysis, context),
        schema_name="llm_answer",
        schema=LLM_ANSWER_SCHEMA,
        max_completion_tokens=MAX_COMPLETION_TOKENS,
        reasoning_effort=settings.reasoning_effort_answer,
    )
