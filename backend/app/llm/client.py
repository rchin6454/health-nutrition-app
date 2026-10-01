"""The only module that talks to Groq (architecture §5.6).

No other module may import `groq`. Callers get the raw JSON string back and validate it
themselves; this module never parses, repairs or retries model output (R1, R7).
"""

import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, cast

import groq
from groq import AsyncGroq
from groq.types.chat import ChatCompletionMessageParam

from app.config import get_settings
from app.llm.rate_limit import BudgetExceeded, Limits, ModelBudget, estimate_tokens, parse_duration

logger = logging.getLogger(__name__)

TIMEOUT_S = 30.0
TEMPERATURE = 0.2
# Completion tokens assumed before a call (reasoning included); observed 170-740 per call.
COMPLETION_RESERVE_TOKENS = 800

LLMErrorKind = Literal[
    "rate_limited",  # Groq answered 429
    "budget_exceeded",  # refused in code before calling Groq, to stay inside its limits
    "api_error",
    "connection_error",
    "timeout",
    "config_error",
]
ReasoningEffort = Literal["low", "medium", "high"]


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMCallError(Exception):
    """A Groq call failed. `kind` doubles as the `failure_type` recorded in `failures`."""

    def __init__(
        self,
        kind: LLMErrorKind,
        detail: str,
        *,
        latency_ms: int | None = None,
        raw_output: str | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind: LLMErrorKind = kind
        self.detail = detail
        self.latency_ms = latency_ms
        # Groq includes the rejected generation in some 400 errors (e.g. json_validate_failed).
        self.raw_output = raw_output
        # For rate_limited / budget_exceeded: when the model accepts calls again, if known.
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class LLMResult:
    content: str | None  # raw JSON string exactly as returned; validated by the caller
    model: str
    latency_ms: int
    usage: dict[str, Any] | None


_client: AsyncGroq | None = None
_budgets: dict[str, ModelBudget] = {}


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        # No hidden retries (R7): a failed call is recorded, never silently repeated.
        _client = AsyncGroq(api_key=get_settings().groq_api_key, max_retries=0, timeout=TIMEOUT_S)
    return _client


def budget_for(model: str) -> ModelBudget:
    """The rate-limit budget of one Groq model (Groq counts limits per model)."""
    budget = _budgets.get(model)
    if budget is None:
        settings = get_settings()
        limits = Limits(
            rpm=settings.groq_rpm,
            rpd=settings.groq_rpd,
            tpm=settings.groq_tpm,
            tpd=settings.groq_tpd,
        )
        budget = _budgets[model] = ModelBudget(limits)
    return budget


def reset_budgets() -> None:
    """Forget all budgets (used by tests)."""
    _budgets.clear()


async def structured_call(
    *,
    model: str,
    messages: list[ChatMessage],
    schema_name: str,
    schema: dict[str, Any],
    max_completion_tokens: int | None = None,
    reasoning_effort: ReasoningEffort | None = None,
) -> LLMResult:
    """Call Groq with a strict JSON-schema response format and return the raw output.

    The call first takes its share of the model's rate-limit budget; if that is not available
    in time, `LLMCallError("budget_exceeded")` is raised and Groq is never called.
    """
    start = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - start) * 1000)

    budget = budget_for(model)
    prompt_chars = sum(len(m["content"]) for m in messages) + len(json.dumps(schema))
    estimate = estimate_tokens(prompt_chars, COMPLETION_RESERVE_TOKENS)
    try:
        reservation = await budget.acquire(estimate, max_wait_s=get_settings().llm_max_wait_s)
    except BudgetExceeded as exc:
        raise LLMCallError(
            "budget_exceeded",
            f"{exc} (model {model}, ~{estimate} tokens; usage {budget.usage()})",
            latency_ms=elapsed_ms(),
            retry_after_s=exc.retry_after_s,
        ) from exc

    options: dict[str, Any] = {}
    if max_completion_tokens is not None:
        options["max_completion_tokens"] = max_completion_tokens
    if reasoning_effort is not None:
        options["reasoning_effort"] = reasoning_effort
    try:
        client = _get_client()
        raw = await client.chat.completions.with_raw_response.create(
            model=model,
            messages=cast(list[ChatCompletionMessageParam], messages),
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            temperature=TEMPERATURE,
            **options,
        )
        budget.observe_headers(raw.headers)
        resp = await raw.parse()
    # Order matters: APITimeoutError subclasses APIConnectionError, RateLimitError subclasses
    # APIStatusError.
    except groq.APITimeoutError as exc:
        raise LLMCallError("timeout", str(exc), latency_ms=elapsed_ms()) from exc
    except groq.APIConnectionError as exc:
        raise LLMCallError("connection_error", str(exc), latency_ms=elapsed_ms()) from exc
    except groq.RateLimitError as exc:
        headers = exc.response.headers
        budget.observe_headers(headers)
        retry_after = _retry_after(headers)
        if retry_after is not None:
            budget.cool_down(retry_after)
        raise LLMCallError(
            "rate_limited",
            _status_detail(exc),
            latency_ms=elapsed_ms(),
            retry_after_s=retry_after,
        ) from exc
    except groq.APIStatusError as exc:
        raise LLMCallError(
            "api_error",
            _status_detail(exc),
            latency_ms=elapsed_ms(),
            raw_output=_failed_generation(exc),
        ) from exc
    except groq.GroqError as exc:  # e.g. missing GROQ_API_KEY
        raise LLMCallError("config_error", str(exc), latency_ms=elapsed_ms()) from exc

    if resp.usage and resp.usage.total_tokens is not None:
        budget.settle(reservation, resp.usage.total_tokens)
    logger.info("groq %s budget after call: %s", model, budget.usage())
    return LLMResult(
        content=resp.choices[0].message.content if resp.choices else None,
        model=resp.model or model,
        latency_ms=elapsed_ms(),
        usage=resp.usage.model_dump() if resp.usage else None,
    )


def _retry_after(headers: Mapping[str, str]) -> float | None:
    return parse_duration(headers.get("retry-after"))


def _status_detail(exc: groq.APIStatusError) -> str:
    return f"HTTP {exc.status_code}: {exc.message}"


def _failed_generation(exc: groq.APIStatusError) -> str | None:
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            value = error.get("failed_generation")
            if isinstance(value, str):
                return value
    return None
