"""Load test: 20 concurrent users chatting (implementation plan §5.3).

Usage (from backend/), against the stub server (tests/load/stub_server.py) or a staging URL:
    uv run --group load locust -f tests/load/locustfile.py --headless \\
        -u 20 -r 5 -t 2m --host http://127.0.0.1:8765
    DATABASE_URL=postgresql://... uv run python -m tests.load.check_failures

Each simulated user keeps one conversation and asks a question every 3-8 s (under the
20/minute per-IP limit). Users send their own X-Forwarded-For address, which the backend trusts
as the last proxy hop; behind a real proxy (Railway) the proxy appends the load generator's
address, so all users share one IP and the per-IP limit applies to them together. Raise
RATE_LIMIT_CHAT on staging for that test.

Pass criteria, checked when the run stops (exit code 1 if any fails):
- p95 latency of POST /api/chat < 6 s
- every HTTP 200 body is a valid ChatResponse, and nothing but 200 (or 429) comes back
The request ids of `error` responses are saved for check_failures.py, which verifies that each
has a `failures` row.
"""

import json
import os
import random
from pathlib import Path
from typing import Any
from uuid import uuid4

from locust import HttpUser, between, events, task
from locust.env import Environment
from pydantic import ValidationError

from app.schemas.answer import ChatResponse
from tests.load.scenarios import SCENARIOS

P95_LIMIT_MS = 6000
RESULTS_FILE = Path(os.environ.get("LOAD_RESULTS_FILE", "tests/load/results/last_run.json"))
QUESTIONS = list(SCENARIOS)

_error_request_ids: list[str] = []
_answer_types: dict[str, int] = {}
_unexpected: dict[str, int] = {}


class ChatUser(HttpUser):
    wait_time = between(3, 8)

    def on_start(self) -> None:
        self.conversation_id = str(uuid4())
        self.ip = f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"  # noqa: S311

    @task
    def ask(self) -> None:
        question = random.choice(QUESTIONS)  # noqa: S311
        with self.client.post(
            "/api/chat",
            json={"conversation_id": self.conversation_id, "message": question},
            headers={"X-Forwarded-For": self.ip},
            name="/api/chat",
            catch_response=True,
        ) as response:
            if response.status_code == 429:
                _unexpected["429"] = _unexpected.get("429", 0) + 1
                response.failure("429 Too Many Requests")
                return
            if response.status_code != 200:
                _unexpected[str(response.status_code)] = (
                    _unexpected.get(str(response.status_code), 0) + 1
                )
                response.failure(f"HTTP {response.status_code}")
                return
            try:
                body = ChatResponse.model_validate(response.json())
            except (ValidationError, ValueError) as exc:
                _unexpected["invalid_body"] = _unexpected.get("invalid_body", 0) + 1
                response.failure(f"invalid ChatResponse: {exc}")
                return
            _answer_types[body.answer_type] = _answer_types.get(body.answer_type, 0) + 1
            if body.answer_type == "error":
                _error_request_ids.append(body.request_id)
                response.failure("error response (recorded failure expected)")
            else:
                response.success()


@events.test_stop.add_listener
def on_test_stop(environment: Environment, **_: Any) -> None:
    stats = environment.stats.get("/api/chat", "POST")
    p95 = stats.get_response_time_percentile(0.95) if stats.num_requests else None
    blocking = {k: v for k, v in _unexpected.items() if k != "429"}
    passed = p95 is not None and p95 < P95_LIMIT_MS and not blocking
    result = {
        "requests": stats.num_requests,
        "p50_ms": stats.get_response_time_percentile(0.5) if stats.num_requests else None,
        "p95_ms": p95,
        "max_ms": stats.max_response_time if stats.num_requests else None,
        "answer_types": _answer_types,
        "unexpected": _unexpected,
        "error_request_ids": _error_request_ids,
        "passed": passed,
    }
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        f"\nload test: {stats.num_requests} requests, p95 {p95} ms (limit {P95_LIMIT_MS}), "
        f"answer types {_answer_types}, unexpected {_unexpected} → "
        f"{'PASS' if passed else 'FAIL'}; details in {RESULTS_FILE}"
    )
    # Decides the exit code: locust's default fails the run on any `error` response, but those
    # are expected here (the stub injects bad output); what matters is that each is recorded.
    environment.process_exit_code = 0 if passed else 1
