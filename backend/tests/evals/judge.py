"""Optional LLM-as-judge rubric: clarity, relevance and unsupported claims (`--judge`).

It costs one answer-model call per graded case, so it is off by default. Its scores are
reported next to the deterministic checks and never decide whether a case passes. Its output is
parsed like any model output: strict schema, no repair; a failure is reported, not retried.
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from app.config import get_settings
from app.llm.client import LLMCallError, structured_call
from app.llm.strict_schema import to_groq_strict
from app.pipeline.prompt_builder import format_context
from app.schemas.answer import ChatResponse, Strict
from app.schemas.context import ContextBundle

JUDGE_PROMPT = Path(__file__).with_name("judge.md").read_text(encoding="utf-8")
JUDGE_PROMPT_VERSION = "judge-v0.1"
MAX_COMPLETION_TOKENS = 2000

Score = Literal[1, 2, 3, 4, 5]


class JudgeScore(Strict):
    clarity: Score
    relevance: Score
    unsupported_claims: list[str]
    rationale: str


JUDGE_SCHEMA = to_groq_strict(JudgeScore)


async def judge(
    question: str, response: ChatResponse, context: ContextBundle | None
) -> JudgeScore | str:
    """The judge's scores, or a short error string if the call or its parse failed."""
    claims = "\n".join(f"- {c.text}" for c in response.claims)
    user = "\n\n".join(
        [
            f"<question>\n{question}\n</question>",
            f"<context>\n{format_context(context)}\n</context>",
            f"<answer>\n{response.answer}\n</answer>",
            f"<claims>\n{claims}\n</claims>",
        ]
    )
    try:
        result = await structured_call(
            model=get_settings().model_answer,
            messages=[
                {"role": "system", "content": JUDGE_PROMPT},
                {"role": "user", "content": user},
            ],
            schema_name="judge_score",
            schema=JUDGE_SCHEMA,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            reasoning_effort="low",
        )
    except LLMCallError as exc:
        return f"judge call failed: {exc.kind}"
    if result.content is None:
        return "judge returned no content"
    try:
        return JudgeScore.model_validate_json(result.content)
    except ValidationError as exc:
        return f"judge output did not parse: {json.dumps(str(exc))[:200]}"
