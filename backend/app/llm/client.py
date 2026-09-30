"""The only module that talks to Groq (architecture §5.6).

No other module may import `groq`. Callers get the raw JSON string back and validate it
themselves; this module never parses, repairs or retries model output (R1, R7).
"""

import time
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, cast

import groq
from groq import AsyncGroq
from groq.types.chat import ChatCompletionMessageParam

from app.config import get_settings

TIMEOUT_S = 30.0
TEMPERATURE = 0.2

LLMErrorKind = Literal["rate_limited", "api_error", "connection_error", "timeout", "config_error"]


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
    ) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind: LLMErrorKind = kind
        self.detail = detail
        self.latency_ms = latency_ms
        # Groq includes the rejected generation in some 400 errors (e.g. json_validate_failed).
        self.raw_output = raw_output


@dataclass(frozen=True)
class LLMResult:
    content: str | None  # raw JSON string exactly as returned; validated by the caller
    model: str
    latency_ms: int
    usage: dict[str, Any] | None


_client: AsyncGroq | None = None


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        # No hidden retries (R7): a failed call is recorded, never silently repeated.
        _client = AsyncGroq(api_key=get_settings().groq_api_key, max_retries=0, timeout=TIMEOUT_S)
    return _client


async def structured_call(
    *, model: str, messages: list[ChatMessage], schema_name: str, schema: dict[str, Any]
) -> LLMResult:
    """Call Groq with a strict JSON-schema response format and return the raw output."""
    start = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - start) * 1000)

    try:
        client = _get_client()
        resp = await client.chat.completions.create(
            model=model,
            messages=cast(list[ChatCompletionMessageParam], messages),
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            temperature=TEMPERATURE,
        )
    # Order matters: APITimeoutError subclasses APIConnectionError, RateLimitError subclasses
    # APIStatusError.
    except groq.APITimeoutError as exc:
        raise LLMCallError("timeout", str(exc), latency_ms=elapsed_ms()) from exc
    except groq.APIConnectionError as exc:
        raise LLMCallError("connection_error", str(exc), latency_ms=elapsed_ms()) from exc
    except groq.RateLimitError as exc:
        raise LLMCallError("rate_limited", _status_detail(exc), latency_ms=elapsed_ms()) from exc
    except groq.APIStatusError as exc:
        raise LLMCallError(
            "api_error",
            _status_detail(exc),
            latency_ms=elapsed_ms(),
            raw_output=_failed_generation(exc),
        ) from exc
    except groq.GroqError as exc:  # e.g. missing GROQ_API_KEY
        raise LLMCallError("config_error", str(exc), latency_ms=elapsed_ms()) from exc

    return LLMResult(
        content=resp.choices[0].message.content if resp.choices else None,
        model=resp.model or model,
        latency_ms=elapsed_ms(),
        usage=resp.usage.model_dump() if resp.usage else None,
    )


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
