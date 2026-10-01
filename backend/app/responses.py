"""Code-built `ChatResponse` objects. Every builder returns a validated `ChatResponse` (R2)."""

import math
from typing import Any

from app.schemas.answer import AnswerType, ChatResponse, LLMAnswer, ResponseCategory

DISCLAIMER = "General information, not medical advice."
EMERGENCY_NOTICE = (
    "This may be a medical emergency. Call 112 (emergency) or 108 (ambulance) now, "
    "or go to the nearest hospital."
)
SYMPTOMS_NOTICE = (
    "If symptoms are severe or getting worse (for example trouble breathing, swelling of the "
    "face or throat, blood in vomit or stool, confusion or signs of dehydration), call 112 or "
    "108 or see a doctor right away."
)
HIGH_RISK_NOTICE = (
    "Pregnant women, infants and young children, older adults and people with weak immunity "
    "are more vulnerable to food-borne illness and nutrient gaps. Check with a doctor or "
    "registered dietitian."
)
OUT_OF_SCOPE_REPLY = (
    "I can only help with questions about food, nutrition and food safety, for example "
    '"How much protein is there in 100 g of paneer?" or "Can I eat rice left out overnight?"'
)


def _build(
    *,
    request_id: str,
    conversation_id: str,
    answer_type: AnswerType,
    category: ResponseCategory,
    answer: str,
    claims: list[dict[str, Any]],
    notices: list[str] | None,
) -> ChatResponse:
    return ChatResponse.model_validate(
        {
            "schema_version": "1.0",
            "request_id": request_id,
            "conversation_id": conversation_id,
            "answer_type": answer_type,
            "category": category,
            "answer": answer,
            "claims": claims,
            "notices": notices if notices is not None else [DISCLAIMER],
        }
    )


def build_answer_response(
    *,
    request_id: str,
    conversation_id: str,
    llm_answer: LLMAnswer,
    category: ResponseCategory = "none",
    notices: list[str] | None = None,
) -> ChatResponse:
    """Wrap a validated model answer. The model's `answer` and `claims` are passed through unedited.

    Raises `pydantic.ValidationError` if the result is not a valid `ChatResponse`; the caller
    records that as `response_build_failed`.
    """
    return _build(
        request_id=request_id,
        conversation_id=conversation_id,
        answer_type="answer",
        category=category,
        answer=llm_answer.answer,
        claims=[c.model_dump() for c in llm_answer.claims],
        notices=notices,
    )


def build_clarification_response(
    *,
    request_id: str,
    conversation_id: str,
    question: str,
    category: ResponseCategory,
    notices: list[str] | None = None,
) -> ChatResponse:
    """The understanding step's clarifying question, returned as the `answer` text."""
    return _build(
        request_id=request_id,
        conversation_id=conversation_id,
        answer_type="clarification",
        category=category,
        answer=question,
        claims=[],
        notices=notices,
    )


def build_out_of_scope_response(
    *, request_id: str, conversation_id: str, notices: list[str] | None = None
) -> ChatResponse:
    return build_referral_response(
        request_id=request_id,
        conversation_id=conversation_id,
        message=OUT_OF_SCOPE_REPLY,
        notices=notices,
    )


def build_referral_response(
    *, request_id: str, conversation_id: str, message: str, notices: list[str] | None = None
) -> ChatResponse:
    """A code-written refusal (blocked topic, medicine question, bad length) with a referral."""
    return _build(
        request_id=request_id,
        conversation_id=conversation_id,
        answer_type="out_of_scope",
        category="out_of_scope",
        answer=message,
        claims=[],
        notices=notices,
    )


GENERIC_ERROR = (
    "Sorry, something went wrong while answering your question. "
    "The problem has been recorded so it can be fixed. Please try again."
)


def busy_message(retry_after_s: float | None) -> str:
    """The error text when the model's rate limit is reached."""
    if retry_after_s is None or retry_after_s > 3600:
        return (
            "The assistant has reached its usage limit for now, so it can't answer this "
            "question. Please try again later."
        )
    if retry_after_s < 90:
        wait = f"{max(1, math.ceil(retry_after_s))} seconds"
    else:
        wait = f"{math.ceil(retry_after_s / 60)} minutes"
    return (
        "The assistant is getting too many questions right now, so it couldn't answer this "
        f"one. Please try again in about {wait}."
    )


def build_error_response(
    *,
    request_id: str,
    conversation_id: str,
    notices: list[str] | None = None,
    message: str | None = None,
) -> ChatResponse:
    """The honest error reply returned after a failure has been recorded."""
    return _build(
        request_id=request_id,
        conversation_id=conversation_id,
        answer_type="error",
        category="none",
        answer=f"{message or GENERIC_ERROR} (Reference: {request_id})",
        claims=[],
        notices=notices,
    )
