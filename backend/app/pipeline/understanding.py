"""Model call 1: question understanding (architecture §5.3). Checks and reports; never repairs."""

from pydantic import ValidationError

from app.config import get_settings
from app.llm.client import LLMResult, structured_call
from app.llm.strict_schema import to_groq_strict
from app.pipeline.prompt_builder import build_understanding_messages
from app.pipeline.validate import ValidationFailure
from app.schemas.analysis import QuestionAnalysis
from app.store.conversations import StoredMessage

QUESTION_ANALYSIS_SCHEMA = to_groq_strict(QuestionAnalysis)
# Hard cap on reasoning + JSON (observed 170-700). Hitting it truncates the JSON, which is
# recorded as a validation failure, never repaired.
MAX_COMPLETION_TOKENS = 1500


async def analyse_question(question: str, history: list[StoredMessage]) -> LLMResult:
    """Raises `LLMCallError` on any Groq failure or when the model's budget is exhausted."""
    settings = get_settings()
    return await structured_call(
        model=settings.model_understanding,
        messages=build_understanding_messages(question, history),
        schema_name="question_analysis",
        schema=QUESTION_ANALYSIS_SCHEMA,
        max_completion_tokens=MAX_COMPLETION_TOKENS,
        reasoning_effort=settings.reasoning_effort_understanding,
    )


def validate_analysis(raw: str | None) -> QuestionAnalysis | ValidationFailure:
    """Parse the raw output exactly as returned (no JSON repair), then check its invariants."""
    if raw is None:
        return ValidationFailure("schema_validation_failed", "model returned no content")
    try:
        analysis = QuestionAnalysis.model_validate_json(raw)
    except ValidationError as exc:
        return ValidationFailure("schema_validation_failed", str(exc))
    if analysis.needs_clarification and not (analysis.clarifying_question or "").strip():
        return ValidationFailure(
            "missing_clarifying_question", "needs_clarification is true but no question was given"
        )
    return analysis
