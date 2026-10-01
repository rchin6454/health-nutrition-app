"""Phase 5 hardening against a real Postgres: the admin failures endpoint, per-IP rate limiting
of POST /api/chat, and input sanitization."""

import json
from typing import Any
from uuid import uuid4

import asyncpg
import httpx
import pytest

from app.config import get_settings
from app.failures import record_failure
from app.schemas.answer import ChatResponse
from tests.conftest import FakeGroq

TOKEN = "test-admin-token"  # noqa: S105 - a test value
GOOD_OUTPUT = json.dumps(
    {
        "answer": "Brown rice has more fibre. This could not be verified against reference data.",
        "claims": [{"text": "Brown rice keeps its bran layer.", "source": None}],
    }
)


@pytest.fixture
def admin_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "admin_token", TOKEN)
    return TOKEN


async def _record(failure_type: str, **overrides: Any) -> None:
    fields: dict[str, Any] = {
        "request_id": str(uuid4()),
        "conversation_id": None,
        "stage": "validation",
        "failure_type": failure_type,
    }
    fields.update(overrides)
    await record_failure(**fields)


async def _get(api: httpx.AsyncClient, header: str | None = TOKEN, **params: Any) -> httpx.Response:
    headers = {"X-Admin-Token": header} if header else {}
    return await api.get("/api/admin/failures", params=params, headers=headers)


# --- GET /api/admin/failures ---


async def test_admin_endpoint_is_hidden_while_no_token_is_configured(
    api: httpx.AsyncClient, pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "admin_token", None)
    assert (await _get(api, header="anything")).status_code == 404


@pytest.mark.parametrize("token", [None, "wrong-token"])
async def test_admin_endpoint_rejects_a_missing_or_wrong_token(
    api: httpx.AsyncClient, pool: asyncpg.Pool, admin_token: str, token: str | None
) -> None:
    assert (await _get(api, header=token)).status_code == 401


async def test_admin_lists_failures_newest_first_with_every_column(
    api: httpx.AsyncClient, pool: asyncpg.Pool, admin_token: str
) -> None:
    cid = str(uuid4())
    await _record("schema_validation_failed", raw_output="{bad", conversation_id=cid)
    await _record("unverified_number", severity="warning", user_message="protein in paneer?")

    resp = await _get(api)

    assert resp.status_code == 200
    body = resp.json()
    assert [f["failure_type"] for f in body["failures"]] == [
        "unverified_number",
        "schema_validation_failed",
    ]
    assert body["next_before_id"] is None
    newest, oldest = body["failures"]
    assert newest["severity"] == "warning"
    assert newest["user_message"] == "protein in paneer?"
    assert oldest["raw_output"] == "{bad"  # exactly as stored
    assert oldest["conversation_id"] == cid


async def test_admin_filters_by_type_stage_severity_and_date(
    api: httpx.AsyncClient, pool: asyncpg.Pool, admin_token: str
) -> None:
    await _record("timeout", stage="upstream_api")
    await _record("unverified_number", severity="warning")
    await _record("schema_validation_failed")
    await pool.execute(
        "UPDATE failures SET created_at = '2026-01-01T00:00:00Z' WHERE failure_type = 'timeout'"
    )

    def types(resp: httpx.Response) -> list[str]:
        assert resp.status_code == 200
        return [f["failure_type"] for f in resp.json()["failures"]]

    assert types(await _get(api, failure_type="timeout")) == ["timeout"]
    assert types(await _get(api, stage="upstream_api")) == ["timeout"]
    assert types(await _get(api, severity="warning")) == ["unverified_number"]
    assert types(await _get(api, since="2026-06-01T00:00:00Z")) == [
        "schema_validation_failed",
        "unverified_number",
    ]
    assert types(await _get(api, until="2026-06-01T00:00:00Z")) == ["timeout"]
    assert (await _get(api, stage="not_a_stage")).status_code == 422


async def test_admin_pages_with_before_id(
    api: httpx.AsyncClient, pool: asyncpg.Pool, admin_token: str
) -> None:
    for i in range(5):
        await _record(f"type_{i}")

    first = (await _get(api, limit=2)).json()
    second = (await _get(api, limit=2, before_id=first["next_before_id"])).json()
    third = (await _get(api, limit=2, before_id=second["next_before_id"])).json()

    pages = [first, second, third]
    assert [[f["failure_type"] for f in p["failures"]] for p in pages] == [
        ["type_4", "type_3"],
        ["type_2", "type_1"],
        ["type_0"],
    ]
    assert third["next_before_id"] is None


# --- Per-IP rate limiting of POST /api/chat ---


async def _chat(api: httpx.AsyncClient, ip: str | None = None) -> httpx.Response:
    headers = {"X-Forwarded-For": ip} if ip else {}
    return await api.post(
        "/api/chat",
        json={"conversation_id": str(uuid4()), "message": "Is brown rice healthy?"},
        headers=headers,
    )


async def test_chat_is_limited_per_ip_and_the_429_is_recorded(
    api: httpx.AsyncClient,
    pool: asyncpg.Pool,
    fake_groq: FakeGroq,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_groq.outcome = GOOD_OUTPUT
    monkeypatch.setattr(get_settings(), "rate_limit_chat", "2/minute")
    monkeypatch.setattr(get_settings(), "groq_tpm", 100_000)  # not the limit under test

    assert (await _chat(api, "203.0.113.7")).status_code == 200
    assert (await _chat(api, "203.0.113.7")).status_code == 200
    calls_before = len(fake_groq.calls)
    limited = await _chat(api, "203.0.113.7")
    # Another client is not affected.
    other = await _chat(api, "198.51.100.1")

    assert limited.status_code == 429
    assert 1 <= int(limited.headers["Retry-After"]) <= 60
    assert limited.json()["detail"].startswith("Too many requests")
    assert limited.json()["retry_after_s"] == int(limited.headers["Retry-After"])
    assert len(fake_groq.calls) == calls_before + 2  # only `other` reached the model
    assert other.status_code == 200
    ChatResponse.model_validate(other.json())

    rows = await pool.fetch("SELECT * FROM failures WHERE failure_type = 'ip_rate_limited'")
    assert len(rows) == 1
    assert rows[0]["severity"] == "warning"
    assert rows[0]["stage"] == "input_gate"
    # Only the two allowed requests from the limited IP and the other IP were stored.
    assert await pool.fetchval("SELECT count(*) FROM conversations") == 3


async def test_a_spoofed_forwarded_for_entry_does_not_dodge_the_limit(
    api: httpx.AsyncClient,
    pool: asyncpg.Pool,
    fake_groq: FakeGroq,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Behind one proxy, only the last X-Forwarded-For entry (written by the proxy) counts.
    fake_groq.outcome = GOOD_OUTPUT
    monkeypatch.setattr(get_settings(), "rate_limit_chat", "1/minute")

    assert (await _chat(api, "1.1.1.1, 203.0.113.7")).status_code == 200
    assert (await _chat(api, "2.2.2.2, 203.0.113.7")).status_code == 429


# --- Input sanitization ---


async def test_control_and_invisible_characters_are_removed_before_storage(
    api: httpx.AsyncClient, pool: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    fake_groq.outcome = GOOD_OUTPUT
    resp = await api.post(
        "/api/chat",
        json={
            "conversation_id": str(uuid4()),
            "message": "  Is brown​ rice\x00 healthy?‮\r\nThanks  ",
        },
    )

    assert resp.status_code == 200
    stored = await pool.fetchval("SELECT content->>'text' FROM messages WHERE role = 'user'")
    assert stored == "Is brown rice healthy?\nThanks"
    understanding = fake_groq.calls[0]["messages"][-1]["content"]
    assert "Is brown rice healthy?\nThanks" in understanding


async def test_over_long_messages_are_rejected_on_the_server(
    api: httpx.AsyncClient, pool: asyncpg.Pool, fake_groq: FakeGroq
) -> None:
    resp = await api.post(
        "/api/chat", json={"conversation_id": str(uuid4()), "message": "a" * 1001}
    )
    assert resp.status_code == 422
    assert fake_groq.calls == []
