"""The real backend with a stubbed Groq client, for load tests (implementation plan §5.3).

Everything except the model runs for real: gates, rate limits, Groq budgets, knowledge lookup
and retrieval, validation, conversation storage and failure recording. The stub answers with
realistic latency, and a share of its answers is invalid JSON, so the test can check that every
error response has a `failures` row.

Usage (from backend/), against a database with the knowledge tables loaded (not production):
    DATABASE_URL=postgresql://... uv run --group load python -m tests.load.stub_server
    STUB_FAILURE_RATE=0.05      share of answer calls that return invalid JSON (default 0.05)
    STUB_REAL_BUDGETS=1         keep the free-tier Groq budgets (default: effectively unlimited)
    PORT=8765

Then run tests/load/locustfile.py against http://127.0.0.1:8765.
"""

import asyncio
import json
import os
import random
import re
from types import SimpleNamespace
from typing import Any

if os.environ.get("STUB_REAL_BUDGETS") != "1":
    # The stub is not Groq: lift its per-model budgets so they don't cap the test.
    for name, value in {
        "GROQ_RPM": "100000",
        "GROQ_RPD": "10000000",
        "GROQ_TPM": "100000000",
        "GROQ_TPD": "1000000000",
    }.items():
        os.environ[name] = value
os.environ.setdefault("GROQ_API_KEY", "stub-never-sent")

import uvicorn

from app.llm import client as llm_client
from app.main import app
from tests.load.scenarios import ANSWER, SCENARIOS

FAILURE_RATE = float(os.environ.get("STUB_FAILURE_RATE", "0.05"))
# Observed Groq latencies (Phase 4/5 runs): understanding ~0.5-1.5 s, answer ~1.5-4 s.
UNDERSTANDING_S = (0.5, 1.5)
ANSWER_S = (1.5, 4.0)
_QUESTION = re.compile(r"<user_question>\n(.*)\n</user_question>", re.DOTALL)


class StubGroq:
    @property
    def chat(self) -> Any:
        return SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=self._create))
        )

    async def _create(self, **kwargs: Any) -> Any:
        is_understanding = kwargs["response_format"]["json_schema"]["name"] == "question_analysis"
        if is_understanding:
            await asyncio.sleep(random.uniform(*UNDERSTANDING_S))  # noqa: S311
            match = _QUESTION.search(kwargs["messages"][-1]["content"])
            question = match.group(1) if match else ""
            content = json.dumps(SCENARIOS.get(question) or SCENARIOS["Write a poem about cars"])
        else:
            await asyncio.sleep(random.uniform(*ANSWER_S))  # noqa: S311
            bad = random.random() < FAILURE_RATE  # noqa: S311
            content = '{"answer": "truncated' if bad else json.dumps(ANSWER)
        usage = {"total_tokens": 2500 if is_understanding else 3200}
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            model=kwargs["model"],
            usage=SimpleNamespace(**usage, model_dump=lambda: usage),
        )

        async def parse() -> Any:
            return completion

        return SimpleNamespace(headers={}, parse=parse)


def main() -> None:
    stub = StubGroq()
    llm_client._get_client = lambda: stub  # type: ignore[assignment,return-value]
    # log_config=None: keep the JSON logging app.main set up (the app is already imported).
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8765")), log_config=None)


if __name__ == "__main__":
    main()
