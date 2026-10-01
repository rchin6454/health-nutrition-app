"""POST /api/chat and the conversation endpoints against a real Postgres and a stubbed Groq."""

import json
from typing import Any
from uuid import uuid4

import asyncpg
import groq
import httpx
import pytest

from app.llm.strict_schema import to_groq_strict
from app.prompts import PROMPT_VERSION
from app.schemas.analysis import QuestionAnalysis
from app.schemas.answer import ChatResponse, LLMAnswer
from tests.conftest import FakeGroq, analysis_json

QUESTION = "Is brown rice healthier than white rice?"
GOOD_OUTPUT = json.dumps(
    {
        # These tests run with no knowledge data loaded, so the answer hedges as the prompt asks
        # (otherwise `unsupported_without_context` is recorded).
        "answer": "**Brown rice has more fibre** than white rice. This could not be verified "
        "against reference data.",
        "claims": [
            {"text": "Brown rice keeps its bran layer, so it has more fibre.", "source": None},
            {"text": "White rice has most of its bran removed.", "source": None},
        ],
    }
)
_REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


async def _post(api: httpx.AsyncClient, conversation_id: str, message: str = QUESTION) -> Any:
    resp = await api.post(
        "/api/chat", json={"conversation_id": conversation_id, "message": message}
    )
    assert resp.status_code == 200
    body = resp.json()
    ChatResponse.model_validate(body)  # every response parses (R2)
    return body


async def _failures(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch("SELECT * FROM failures ORDER BY id")


async def test_happy_path_stores_one_conversation_and_two_messages(
    api: httpx.AsyncClient, pool: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.outcome = GOOD_OUTPUT
    cid = str(uuid4())

    body = await _post(api, cid)

    assert body["answer_type"] == "answer"
    assert body["category"] == "nutrition"  # from the analysis, set by code
    assert body["conversation_id"] == cid
    assert body["answer"] == json.loads(GOOD_OUTPUT)["answer"]
    assert [c["source"] for c in body["claims"]] == [None, None]
    assert body["notices"] == ["General information, not medical advice."]

    assert await pool.fetchval("SELECT count(*) FROM conversations") == 1
    rows = await pool.fetch("SELECT * FROM messages ORDER BY id")
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content"] == {"text": QUESTION}
    assert rows[0]["analysis"] == json.loads(analysis_json())
    assert rows[1]["content"] == body  # the exact response returned
    assert rows[1]["prompt_version"] == PROMPT_VERSION
    assert await _failures(pool) == []

    understanding, answer = fake_groq.calls
    assert understanding["model"] == "openai/gpt-oss-20b"
    assert understanding["response_format"]["json_schema"]["strict"] is True
    assert understanding["response_format"]["json_schema"]["schema"] == to_groq_strict(
        QuestionAnalysis
    )
    assert understanding["messages"][-1]["content"] == (
        f"<user_question>\n{QUESTION}\n</user_question>"
    )
    assert answer["model"] == "openai/gpt-oss-120b"
    assert answer["response_format"]["json_schema"]["strict"] is True
    assert answer["response_format"]["json_schema"]["schema"] == to_groq_strict(LLMAnswer)
    final = answer["messages"][-1]["content"]
    assert final.startswith("<question_analysis>\ncategory: nutrition\n")
    assert final.endswith(f"<user_question>\n{QUESTION}\n</user_question>")


@pytest.mark.parametrize(
    ("outcome", "stage", "failure_type"),
    [
        ('{"answer": "Brown rice is', "validation", "schema_validation_failed"),
        (
            json.dumps({"answer": "a", "claims": [{"text": "t", "source": "x"}]}),
            "validation",
            "source_not_null",
        ),
        (json.dumps({"answer": "a", "claims": []}), "validation", "empty_claims"),
        (groq.APITimeoutError(request=_REQ), "upstream_api", "timeout"),
        (
            groq.NotFoundError(
                "The model `bad-model` does not exist",
                response=httpx.Response(404, request=_REQ),
                body=None,
            ),
            "upstream_api",
            "api_error",
        ),
    ],
)
async def test_failures_are_recorded_and_returned_as_error(
    api: httpx.AsyncClient,
    pool: asyncpg.Pool,
    fake_groq: FakeGroq,
    outcome: str | Exception,
    stage: str,
    failure_type: str,
) -> None:
    fake_groq.outcome = outcome
    cid = str(uuid4())

    body = await _post(api, cid)

    assert body["answer_type"] == "error"
    assert body["claims"] == []
    assert body["request_id"] in body["answer"]

    [failure] = await _failures(pool)
    assert failure["stage"] == stage
    assert failure["failure_type"] == failure_type
    assert failure["severity"] == "error"
    assert str(failure["request_id"]) == body["request_id"]
    assert str(failure["conversation_id"]) == cid
    assert failure["user_message"] == QUESTION
    assert failure["prompt_version"] == PROMPT_VERSION
    assert failure["model"] == "openai/gpt-oss-120b"
    if isinstance(outcome, str):
        assert failure["raw_output"] == outcome  # stored exactly as returned
    # Understanding once, answer once: no silent retry.
    assert fake_groq.schema_names == ["question_analysis", "llm_answer"]

    # The error response is still stored, so a reload shows the same bubble.
    roles = await pool.fetch("SELECT role, content FROM messages ORDER BY id")
    assert [r["role"] for r in roles] == ["user", "assistant"]
    assert roles[1]["content"] == body


async def test_history_is_reloaded_in_order_and_sent_as_context(
    api: httpx.AsyncClient, pool: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.outcome = GOOD_OUTPUT
    cid = str(uuid4())
    first = await _post(api, cid)
    second = await _post(api, cid, "What about red rice?")

    # Both calls of the second turn saw the first turn (answer text only).
    for call in fake_groq.calls[2:]:
        assert call["messages"][1] == {"role": "user", "content": QUESTION}
        assert call["messages"][2] == {"role": "assistant", "content": first["answer"]}

    resp = await api.get(f"/api/conversations/{cid}")
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert [(m["role"], m.get("text")) for m in messages] == [
        ("user", QUESTION),
        ("assistant", None),
        ("user", "What about red rice?"),
        ("assistant", None),
    ]
    assert messages[1]["response"] == first
    assert messages[3]["response"] == second
    assert await pool.fetchval("SELECT count(*) FROM conversations") == 1


async def test_delete_conversation(
    api: httpx.AsyncClient, pool: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.outcome = GOOD_OUTPUT
    cid = str(uuid4())
    await _post(api, cid)

    assert (await api.delete(f"/api/conversations/{cid}")).status_code == 204
    assert (await api.get(f"/api/conversations/{cid}")).status_code == 404
    assert (await api.delete(f"/api/conversations/{cid}")).status_code == 404
    assert await pool.fetchval("SELECT count(*) FROM messages") == 0


async def test_unknown_conversation_is_404(api: httpx.AsyncClient, pool: asyncpg.Pool) -> None:
    assert (await api.get(f"/api/conversations/{uuid4()}")).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"conversation_id": str(uuid4()), "message": "   "},
        {"conversation_id": str(uuid4()), "message": "x" * 1001},
        {"conversation_id": "not-a-uuid", "message": "hi"},
        {"message": "hi"},
    ],
)
async def test_malformed_requests_are_422_and_never_call_groq(
    api: httpx.AsyncClient, pool: asyncpg.Pool, fake_groq: FakeGroq, payload: dict[str, Any]
) -> None:
    resp = await api.post("/api/chat", json=payload)
    assert resp.status_code == 422
    assert fake_groq.calls == []


async def test_storage_outage_returns_recorded_error(
    api: httpx.AsyncClient, fake_groq: FakeGroq, capsys: pytest.CaptureFixture[str]
) -> None:
    # No `pool` fixture: the database is unavailable.
    fake_groq.outcome = GOOD_OUTPUT
    body = await _post(api, str(uuid4()))

    assert body["answer_type"] == "error"
    assert fake_groq.calls == []
    assert "FAILURE_RECORD_WRITE_FAILED" in capsys.readouterr().err
