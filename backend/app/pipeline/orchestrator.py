"""Runs one chat turn through the lifecycle in architecture §4:

input gate → understanding → classification gate → knowledge lookup (nutrition facts, safety
rules, guidance passages) → prompt builder → answer call → validation → output gate → soft
checks.

Every path returns a validated `ChatResponse`; every failure is recorded first (R2, R7).
"""

import time
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Literal

from app.config import get_settings
from app.failures import Severity, Stage, record_failure
from app.knowledge.context import build_context, uses_knowledge
from app.llm.client import LLMCallError, LLMResult
from app.pipeline.answer import generate_answer
from app.pipeline.understanding import analyse_question, validate_analysis
from app.pipeline.validate import (
    ValidationFailure,
    parse_llm_answer,
    unsupported_without_context,
    unverified_numbers,
)
from app.prompts import PROMPT_VERSION
from app.responses import (
    build_clarification_response,
    build_error_response,
    build_out_of_scope_response,
    build_referral_response,
    busy_message,
)
from app.schemas.analysis import QuestionAnalysis
from app.schemas.answer import ChatResponse
from app.schemas.context import ContextBundle
from app.scope.classification_gate import check_classification
from app.scope.input_gate import check_input
from app.scope.output_gate import finalize_answer, notices_for
from app.store.conversations import StoredMessage


@dataclass(frozen=True)
class TurnResult:
    response: ChatResponse
    model: str | None  # the last model called, if any
    prompt_version: str
    latency_ms: int
    usage: dict[str, Any] | None  # {"understanding": ..., "answer": ...}
    analysis: QuestionAnalysis | None  # stored on the user's message row
    context: ContextBundle | None  # stored on the assistant's message row (context_snapshot)


async def run_turn(
    *, request_id: str, conversation_id: str, message: str, history: list[StoredMessage]
) -> TurnResult:
    start = time.perf_counter()
    settings = get_settings()
    model: str | None = None
    usage: dict[str, Any] = {}
    analysis: QuestionAnalysis | None = None
    context: ContextBundle | None = None

    def result(response: ChatResponse) -> TurnResult:
        return TurnResult(
            response=response,
            model=model,
            prompt_version=PROMPT_VERSION,
            latency_ms=int((time.perf_counter() - start) * 1000),
            usage=usage or None,
            analysis=analysis,
            context=context,
        )

    async def record(
        stage: Stage,
        failure_type: str,
        *,
        severity: Severity = "error",
        raw_output: str | None = None,
        detail: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        await record_failure(
            request_id=request_id,
            conversation_id=conversation_id,
            stage=stage,
            failure_type=failure_type,
            severity=severity,
            model=model,
            prompt_version=PROMPT_VERSION,
            user_message=message,
            raw_output=raw_output,
            error_detail=detail,
            latency_ms=latency_ms,
        )

    # --- Gate 1: input gate (no model call yet) ---
    gate = check_input(message)
    if gate.injection:
        await record("input_gate", "injection_suspected", severity="warning", detail=gate.injection)
    notices = notices_for(emergency=gate.emergency, analysis=None)

    def error(message: str | None = None) -> TurnResult:
        return result(
            build_error_response(
                request_id=request_id,
                conversation_id=conversation_id,
                notices=notices,
                message=message,
            )
        )

    if gate.rejection:
        await record(
            "input_gate",
            gate.rejection.reason,
            severity="scope_block",
            detail=gate.rejection.detail,
        )
        return result(
            build_referral_response(
                request_id=request_id,
                conversation_id=conversation_id,
                message=gate.rejection.reply,
                notices=notices,
            )
        )

    async def call(
        label: Literal["understanding", "answer"], pending: Awaitable[LLMResult]
    ) -> LLMResult | TurnResult:
        """Await one model call; on a failure, record it and return the error turn."""
        try:
            llm_result = await pending
        except LLMCallError as exc:
            await record(
                "upstream_api",
                exc.kind,
                raw_output=exc.raw_output,
                detail=f"{label} call: {exc.detail}",
                latency_ms=exc.latency_ms,
            )
            if exc.kind in ("rate_limited", "budget_exceeded"):
                return error(busy_message(exc.retry_after_s))
            return error()
        usage[label] = llm_result.usage
        return llm_result

    # --- Model call 1: understanding ---
    model = settings.model_understanding
    understood = await call("understanding", analyse_question(message, history))
    if isinstance(understood, TurnResult):
        return understood
    model = understood.model
    parsed_analysis = validate_analysis(understood.content)
    if isinstance(parsed_analysis, ValidationFailure):
        await record(
            "understanding",
            parsed_analysis.failure_type,
            raw_output=understood.content,
            detail=parsed_analysis.detail,
            latency_ms=understood.latency_ms,
        )
        return error()
    analysis = parsed_analysis
    notices = notices_for(emergency=gate.emergency, analysis=analysis)

    # --- Gate 2: classification gate ---
    decision = check_classification(analysis)
    if decision.action == "out_of_scope":
        return result(
            build_out_of_scope_response(
                request_id=request_id, conversation_id=conversation_id, notices=notices
            )
        )
    if decision.action == "invalid_category":
        await record(
            "understanding",
            "invalid_category",
            raw_output=understood.content,
            detail=f"category {analysis.category!r} is not allowed",
        )
        return error()
    if decision.action == "referral":
        assert decision.reply is not None  # noqa: S101 - set by the gate for referrals
        return result(
            build_referral_response(
                request_id=request_id,
                conversation_id=conversation_id,
                message=decision.reply,
                notices=notices,
            )
        )
    if decision.action == "clarify":
        assert decision.reply is not None  # noqa: S101 - validate_analysis guarantees it
        return result(
            build_clarification_response(
                request_id=request_id,
                conversation_id=conversation_id,
                question=decision.reply,
                category=analysis.category,
                notices=notices,
            )
        )

    # --- Knowledge lookup: nutrition facts, safety rules, guidance passages ---
    if uses_knowledge(analysis):
        lookup_start = time.perf_counter()
        try:
            knowledge = await build_context(analysis, message)
        except Exception as exc:
            # Recorded, then the answer runs with no context: the prompt makes the model say the
            # data could not be verified, rather than failing the whole turn.
            await record(
                "retrieval",
                "knowledge_lookup_failed",
                detail=repr(exc),
                latency_ms=int((time.perf_counter() - lookup_start) * 1000),
            )
        else:
            context = knowledge.context
            # A failed step is recorded; the facts from the other steps are still used.
            for lookup_error in knowledge.errors:
                await record(
                    "retrieval",
                    lookup_error.failure_type,
                    detail=lookup_error.detail,
                    latency_ms=int((time.perf_counter() - lookup_start) * 1000),
                )

    # --- Model call 2: answer ---
    model = settings.model_answer
    answered = await call("answer", generate_answer(message, history, analysis, context))
    if isinstance(answered, TurnResult):
        return answered
    model = answered.model

    # --- Validation + Gate 3: output gate ---
    llm_answer = parse_llm_answer(answered.content)
    final = (
        llm_answer
        if isinstance(llm_answer, ValidationFailure)
        else finalize_answer(
            request_id=request_id,
            conversation_id=conversation_id,
            llm_answer=llm_answer,
            analysis=analysis,
            emergency=gate.emergency,
        )
    )
    if isinstance(final, ValidationFailure):
        await record(
            "validation",
            final.failure_type,
            raw_output=answered.content,
            detail=final.detail,
            latency_ms=answered.latency_ms,
        )
        return error()

    # --- Soft checks: warnings only; the response is returned unchanged ---
    if not isinstance(llm_answer, ValidationFailure):
        if context is not None:
            unverified = unverified_numbers(llm_answer, context, message)
            if unverified:
                await record(
                    "validation",
                    "unverified_number",
                    severity="warning",
                    raw_output=answered.content,
                    detail="; ".join(unverified),
                    latency_ms=answered.latency_ms,
                )
        unsupported = unsupported_without_context(llm_answer, context)
        if unsupported:
            await record(
                "validation",
                "unsupported_without_context",
                severity="warning",
                raw_output=answered.content,
                detail=unsupported,
                latency_ms=answered.latency_ms,
            )
    return result(final)
