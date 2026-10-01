"""Phase 5 hardening helpers: sanitization, client IP, JSON logs, failure types."""

import asyncio
import json
import logging

import asyncpg
import pytest
from starlette.requests import Request

from app.api.rate_limit import client_ip
from app.config import get_settings
from app.failures import storage_failure_type
from app.logging_config import JsonFormatter, bind_request
from app.schemas.conversation import sanitize_message


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("paneer protein", "paneer protein"),
        ("line one\r\nline two\rthree", "line one\nline two\nthree"),
        ("tab\tand\nnewline kept", "tab\tand\nnewline kept"),
        ("nul\x00 bell\x07 esc\x1b del\x7f c1\x85", "nul bell esc del c1"),
        ("zero​width‍ join﻿", "zerowidth join"),
        ("bidi ‮override‬ ⁦isolate⁩", "bidi override isolate"),
        ("dahi chawal 🍚", "dahi chawal 🍚"),
        ("café", "café"),  # NFC
    ],
)
def test_sanitize_message(raw: str, clean: str) -> None:
    assert sanitize_message(raw) == clean


def _request(forwarded: str | None, host: str = "10.0.0.5") -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "headers": headers, "client": (host, 1234)})


@pytest.mark.parametrize(
    ("hops", "forwarded", "expected"),
    [
        (1, None, "10.0.0.5"),  # no proxy header: the socket address
        (1, "203.0.113.7", "203.0.113.7"),
        (1, "1.1.1.1, 203.0.113.7", "203.0.113.7"),  # left entries are client-written
        (2, "1.1.1.1, 203.0.113.7, 10.1.1.1", "203.0.113.7"),
        (3, "203.0.113.7", "203.0.113.7"),  # fewer entries than hops: the leftmost
        (0, "203.0.113.7", "10.0.0.5"),  # no trusted proxy: the header is ignored
    ],
)
def test_client_ip(
    monkeypatch: pytest.MonkeyPatch, hops: int, forwarded: str | None, expected: str
) -> None:
    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", hops)
    assert client_ip(_request(forwarded)) == expected


def test_json_formatter_adds_request_ids_and_fields() -> None:
    async def log_inside_a_request() -> str:
        bind_request("req-1", "conv-1")
        record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "turn %s", ("ok",), None)
        record.fields = {"stage_ms": {"answer": 1200}, "category": "nutrition"}
        return JsonFormatter().format(record)

    entry = json.loads(asyncio.run(log_inside_a_request()))

    assert entry["message"] == "turn ok"
    assert entry["level"] == "INFO"
    assert entry["request_id"] == "req-1"
    assert entry["conversation_id"] == "conv-1"
    assert entry["stage_ms"] == {"answer": 1200}
    assert entry["category"] == "nutrition"


def test_json_formatter_outside_a_request_has_no_ids() -> None:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "startup", (), None)
    entry = json.loads(JsonFormatter().format(record))
    assert "request_id" not in entry
    assert entry["message"] == "startup"


@pytest.mark.parametrize(
    ("exc", "failure_type"),
    [
        (TimeoutError(), "db_timeout"),
        (asyncpg.QueryCanceledError("canceling statement due to statement timeout"), "db_timeout"),
        (asyncpg.UndefinedTableError("no table"), "storage_error"),
        (OSError("connection refused"), "storage_error"),
    ],
)
def test_storage_failure_type(exc: Exception, failure_type: str) -> None:
    assert storage_failure_type(exc) == failure_type
