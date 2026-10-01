import groq
import httpx
import pytest

from app.config import Settings
from app.llm import client
from app.llm.client import LLMCallError, structured_call
from app.llm.strict_schema import to_groq_strict
from app.schemas.answer import LLMAnswer
from tests.conftest import FakeGroq

_REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def _status(code: int) -> httpx.Response:
    return httpx.Response(code, request=_REQ)


async def _call() -> None:
    await structured_call(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        schema_name="llm_answer",
        schema=to_groq_strict(LLMAnswer),
    )


async def test_sends_strict_json_schema_and_returns_raw_content(fake_groq: FakeGroq) -> None:
    fake_groq.outcome = '{"answer": "a", "claims": []}'
    result = await structured_call(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        schema_name="llm_answer",
        schema=to_groq_strict(LLMAnswer),
    )

    assert result.content == '{"answer": "a", "claims": []}'  # untouched
    [call] = fake_groq.calls
    assert call["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "llm_answer", "strict": True, "schema": to_groq_strict(LLMAnswer)},
    }
    assert "stream" not in call


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (groq.APITimeoutError(request=_REQ), "timeout"),
        (groq.APIConnectionError(request=_REQ), "connection_error"),
        (groq.RateLimitError("slow down", response=_status(429), body=None), "rate_limited"),
        (groq.APIStatusError("model not found", response=_status(404), body=None), "api_error"),
        (groq.GroqError("missing api key"), "config_error"),
    ],
)
async def test_groq_errors_become_typed_llm_call_errors(
    fake_groq: FakeGroq, exc: Exception, kind: str
) -> None:
    fake_groq.outcome = exc
    with pytest.raises(LLMCallError) as info:
        await _call()
    assert info.value.kind == kind
    assert len(fake_groq.calls) == 1  # never retried


async def test_failed_generation_is_kept_as_raw_output(fake_groq: FakeGroq) -> None:
    body = {"error": {"code": "json_validate_failed", "failed_generation": '{"answer": 1}'}}
    fake_groq.outcome = groq.BadRequestError("bad", response=_status(400), body=body)
    with pytest.raises(LLMCallError) as info:
        await _call()
    assert info.value.kind == "api_error"
    assert info.value.raw_output == '{"answer": 1}'


def test_client_has_no_retries_and_30s_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "_client", None)
    monkeypatch.setattr(client, "get_settings", lambda: Settings(groq_api_key="test-key"))

    groq_client = client._get_client()
    assert groq_client.max_retries == 0
    assert groq_client.timeout == 30.0


async def test_sends_token_cap_and_reasoning_effort(fake_groq: FakeGroq) -> None:
    fake_groq.outcome = "{}"
    await structured_call(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        schema_name="llm_answer",
        schema=to_groq_strict(LLMAnswer),
        max_completion_tokens=1500,
        reasoning_effort="low",
    )
    [call] = fake_groq.calls
    assert call["max_completion_tokens"] == 1500
    assert call["reasoning_effort"] == "low"


async def test_real_usage_and_groq_headers_update_the_budget(fake_groq: FakeGroq) -> None:
    fake_groq.outcome = "{}"
    fake_groq.total_tokens = 1234
    fake_groq.headers = {"x-ratelimit-remaining-tokens": "10", "x-ratelimit-reset-tokens": "30s"}
    await _call()

    assert client.budget_for("m").usage()["tokens_minute"] == 1234
    # Groq says only 10 tokens are left this minute, 30 s away: refused without calling Groq.
    with pytest.raises(LLMCallError) as info:
        await _call()
    assert info.value.kind == "budget_exceeded"
    assert "tpm" in info.value.detail
    assert info.value.retry_after_s == pytest.approx(30, abs=1)
    assert len(fake_groq.calls) == 1


async def test_exhausted_budget_never_calls_groq(fake_groq: FakeGroq) -> None:
    fake_groq.outcome = "{}"
    client.budget_for("m").seed([(60.0 * 60, 200_000)])  # today's tokens are used up

    with pytest.raises(LLMCallError) as info:
        await _call()
    assert info.value.kind == "budget_exceeded"
    assert "tpd" in info.value.detail
    assert fake_groq.calls == []


async def test_429_retry_after_puts_the_model_in_cooldown(fake_groq: FakeGroq) -> None:
    response = httpx.Response(429, request=_REQ, headers={"retry-after": "42"})
    fake_groq.outcome = groq.RateLimitError("slow down", response=response, body=None)

    with pytest.raises(LLMCallError) as info:
        await _call()
    assert info.value.kind == "rate_limited"
    assert info.value.retry_after_s == 42

    # The next call is refused in code instead of hitting Groq again.
    with pytest.raises(LLMCallError) as info:
        await _call()
    assert info.value.kind == "budget_exceeded"
    assert "cooldown" in info.value.detail
    assert len(fake_groq.calls) == 1


async def test_budgets_are_per_model(fake_groq: FakeGroq) -> None:
    fake_groq.outcome = "{}"
    client.budget_for("other-model").seed([(60.0, 200_000)])
    await _call()  # model "m" is unaffected
    assert len(fake_groq.calls) == 1
