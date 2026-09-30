"""Code-built `ChatResponse` objects. Every builder returns a validated `ChatResponse` (R2)."""

from app.schemas.answer import ChatResponse, LLMAnswer, ResponseCategory

DISCLAIMER = "General information, not medical advice."


def build_answer_response(
    *,
    request_id: str,
    conversation_id: str,
    llm_answer: LLMAnswer,
    category: ResponseCategory = "none",
) -> ChatResponse:
    """Wrap a validated model answer. The model's `answer` and `claims` are passed through unedited.

    Raises `pydantic.ValidationError` if the result is not a valid `ChatResponse`; the caller
    records that as `response_build_failed`.
    """
    return ChatResponse.model_validate(
        {
            "schema_version": "1.0",
            "request_id": request_id,
            "conversation_id": conversation_id,
            "answer_type": "answer",
            "category": category,
            "answer": llm_answer.answer,
            "claims": [c.model_dump() for c in llm_answer.claims],
            "notices": [DISCLAIMER],
        }
    )


def build_error_response(*, request_id: str, conversation_id: str) -> ChatResponse:
    """The honest error reply returned after a failure has been recorded."""
    return ChatResponse.model_validate(
        {
            "schema_version": "1.0",
            "request_id": request_id,
            "conversation_id": conversation_id,
            "answer_type": "error",
            "category": "none",
            "answer": (
                "Sorry, something went wrong while answering your question. "
                "The problem has been recorded so it can be fixed. Please try again. "
                f"(Reference: {request_id})"
            ),
            "claims": [],
            "notices": [DISCLAIMER],
        }
    )
