import json

import pytest

from app.pipeline.validate import (
    MAX_ANSWER_CHARS,
    MAX_CLAIM_CHARS,
    MAX_CLAIMS,
    ValidationFailure,
    validate_llm_output,
)
from app.schemas.answer import LLMAnswer


def _raw(answer: str = "Brown rice has more fibre.", claims: list[object] | None = None) -> str:
    if claims is None:
        claims = [{"text": "Brown rice keeps its bran layer.", "source": None}]
    return json.dumps({"answer": answer, "claims": claims})


def test_valid_output_parses() -> None:
    result = validate_llm_output(_raw())
    assert isinstance(result, LLMAnswer)
    assert result.claims[0].source is None


@pytest.mark.parametrize(
    ("raw", "failure_type"),
    [
        (None, "schema_validation_failed"),
        ("", "schema_validation_failed"),
        ("not json", "schema_validation_failed"),
        ('{"answer": "a", "claims": [', "schema_validation_failed"),  # truncated: no repair
        ('{"answer": "a"}', "schema_validation_failed"),
        ('{"answer": "a", "claims": [], "extra": 1}', "schema_validation_failed"),
        (_raw(claims=[{"text": "t", "source": "x"}]), "source_not_null"),
        (_raw(claims=[{"text": "t", "source": {"url": "x"}}]), "source_not_null"),
        (_raw(answer="   "), "empty_answer"),
        (_raw(claims=[]), "empty_claims"),
        (_raw(claims=[{"text": " ", "source": None}]), "empty_claims"),
        (_raw(answer="a" * (MAX_ANSWER_CHARS + 1)), "length_exceeded"),
        (_raw(claims=[{"text": "t", "source": None}] * (MAX_CLAIMS + 1)), "length_exceeded"),
        (_raw(claims=[{"text": "t" * (MAX_CLAIM_CHARS + 1), "source": None}]), "length_exceeded"),
    ],
)
def test_invalid_output_is_reported(raw: str | None, failure_type: str) -> None:
    result = validate_llm_output(raw)
    assert isinstance(result, ValidationFailure)
    assert result.failure_type == failure_type


def test_limits_are_inclusive() -> None:
    raw = _raw(
        answer="a" * MAX_ANSWER_CHARS,
        claims=[{"text": "t" * MAX_CLAIM_CHARS, "source": None}] * MAX_CLAIMS,
    )
    assert isinstance(validate_llm_output(raw), LLMAnswer)
