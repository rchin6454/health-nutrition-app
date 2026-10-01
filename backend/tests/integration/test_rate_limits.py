"""Groq rate limits end to end: honest busy errors, recorded failures, seeding from storage."""

import json
from uuid import uuid4

import asyncpg
import groq
import httpx

from app.llm import client as llm_client
from app.main import seed_llm_budgets
from app.schemas.answer import ChatResponse
from tests.conftest import FakeGroq

UNDERSTANDING = "openai/gpt-oss-20b"
ANSWER = "openai/gpt-oss-120b"
GOOD_ANSWER = json.dumps(
    {"answer": "Brown rice has more fibre.", "claims": [{"text": "t", "source": None}]}
)
_REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


async def _post(api: httpx.AsyncClient, cid: str | None = None) -> dict[str, object]:
    resp = await api.post(
        "/api/chat",
        json={"conversation_id": cid or str(uuid4()), "message": "Is brown rice healthy?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    ChatResponse.model_validate(body)
    return body  # type: ignore[no-any-return]


async def test_exhausted_daily_budget_returns_a_busy_error_without_calling_groq(
    api: httpx.AsyncClient, pool: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    llm_client.budget_for(UNDERSTANDING).seed([(3600.0, 200_000)])

    body = await _post(api)

    assert fake_groq.calls == []
    assert body["answer_type"] == "error"
    assert "usage limit" in str(body["answer"])
    assert str(body["request_id"]) in str(body["answer"])

    [failure] = await pool.fetch("SELECT * FROM failures")
    assert failure["stage"] == "upstream_api"
    assert failure["failure_type"] == "budget_exceeded"
    assert failure["model"] == UNDERSTANDING
    assert "tpd" in failure["error_detail"]


async def test_answer_model_budget_is_checked_separately(
    api: httpx.AsyncClient, pool: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.outcome = GOOD_ANSWER
    llm_client.budget_for(ANSWER).cool_down(20)

    body = await _post(api)

    assert fake_groq.schema_names == ["question_analysis"]  # the answer call was never sent
    assert body["answer_type"] == "error"
    assert "try again in about 20 seconds" in str(body["answer"])
    [failure] = await pool.fetch("SELECT * FROM failures")
    assert (failure["failure_type"], failure["model"]) == ("budget_exceeded", ANSWER)


async def test_groq_429_is_recorded_and_the_user_is_told_when_to_retry(
    api: httpx.AsyncClient, pool: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    response = httpx.Response(429, request=_REQ, headers={"retry-after": "3"})
    fake_groq.analysis = groq.RateLimitError("slow down", response=response, body=None)

    body = await _post(api)

    assert "try again in about 3 seconds" in str(body["answer"])
    [failure] = await pool.fetch("SELECT * FROM failures")
    assert failure["failure_type"] == "rate_limited"
    assert len(fake_groq.calls) == 1  # never retried


async def test_usage_is_stored_and_seeds_the_budgets_after_a_restart(
    api: httpx.AsyncClient, pool: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.outcome = GOOD_ANSWER
    fake_groq.total_tokens = 1900
    await _post(api)
    [usage] = await pool.fetchval("SELECT array_agg(usage) FROM messages WHERE role = 'assistant'")
    assert usage == {"understanding": {"total_tokens": 1900}, "answer": {"total_tokens": 1900}}

    # A Phase 1 row stored the answer call's usage directly.
    cid = uuid4()
    await pool.execute("INSERT INTO conversations (id) VALUES ($1)", cid)
    await pool.execute(
        "INSERT INTO messages (conversation_id, request_id, role, content, usage) "
        "VALUES ($1, $2, 'assistant', '{}', $3)",
        cid,
        uuid4(),
        {"total_tokens": 1500, "prompt_tokens": 1000},
    )

    llm_client.reset_budgets()  # a restart forgets everything in memory...
    await seed_llm_budgets()  # ...and startup re-reads it from storage

    assert llm_client.budget_for(UNDERSTANDING).usage()["tokens_day"] == 1900
    assert llm_client.budget_for(ANSWER).usage()["tokens_day"] == 1900 + 1500
    assert llm_client.budget_for(ANSWER).usage()["requests_day"] == 2
