"""Runs one chat turn through the pipeline. Phase 1: answer call → validation → response.

Every path returns a validated `ChatResponse`; every failure is recorded first (R2, R7).
"""

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.failures import record_failure
from app.llm.client import LLMCallError
from app.pipeline.answer import generate_answer
from app.pipeline.validate import ValidationFailure, validate_llm_output
from app.prompts import PROMPT_VERSION
from app.responses import build_answer_response, build_error_response
from app.schemas.answer import ChatResponse
from app.store.conversations import StoredMessage


@dataclass(frozen=True)
class TurnResult:
    response: ChatResponse
    model: str | None
    prompt_version: str
    latency_ms: int | None
    usage: dict[str, Any] | None


async def run_turn(
    *, request_id: str, conversation_id: str, message: str, history: list[StoredMessage]
) -> TurnResult:
    model = get_settings().model_answer

    def error_result(latency_ms: int | None = None) -> TurnResult:
        return TurnResult(
            response=build_error_response(request_id=request_id, conversation_id=conversation_id),
            model=model,
            prompt_version=PROMPT_VERSION,
            latency_ms=latency_ms,
            usage=None,
        )

    try:
        result = await generate_answer(message, history)
    except LLMCallError as exc:
        await record_failure(
            request_id=request_id,
            conversation_id=conversation_id,
            stage="upstream_api",
            failure_type=exc.kind,
            model=model,
            prompt_version=PROMPT_VERSION,
            user_message=message,
            raw_output=exc.raw_output,
            error_detail=exc.detail,
            latency_ms=exc.latency_ms,
        )
        return error_result(exc.latency_ms)

    parsed = validate_llm_output(result.content)
    if isinstance(parsed, ValidationFailure):
        await record_failure(
            request_id=request_id,
            conversation_id=conversation_id,
            stage="validation",
            failure_type=parsed.failure_type,
            model=result.model,
            prompt_version=PROMPT_VERSION,
            user_message=message,
            raw_output=result.content,
            error_detail=parsed.detail,
            latency_ms=result.latency_ms,
        )
        return error_result(result.latency_ms)

    try:
        response = build_answer_response(
            request_id=request_id, conversation_id=conversation_id, llm_answer=parsed
        )
    except ValidationError as exc:
        await record_failure(
            request_id=request_id,
            conversation_id=conversation_id,
            stage="validation",
            failure_type="response_build_failed",
            model=result.model,
            prompt_version=PROMPT_VERSION,
            user_message=message,
            raw_output=result.content,
            error_detail=str(exc),
            latency_ms=result.latency_ms,
        )
        return error_result(result.latency_ms)

    return TurnResult(
        response=response,
        model=result.model,
        prompt_version=PROMPT_VERSION,
        latency_ms=result.latency_ms,
        usage=result.usage,
    )
