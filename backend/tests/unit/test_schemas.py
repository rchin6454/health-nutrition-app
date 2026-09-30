import pytest
from pydantic import BaseModel, ValidationError

from app.llm.strict_schema import to_groq_strict
from app.schemas.answer import ChatResponse, Claim, LLMAnswer, Strict

# The JSON Schema sent to Groq for LLMAnswer, exactly as in architecture §6.2.
EXPECTED_LLM_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source": {"type": "null"},
                },
                "required": ["text", "source"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["answer", "claims"],
    "additionalProperties": False,
}


def test_llm_answer_strict_schema_matches_architecture() -> None:
    assert to_groq_strict(LLMAnswer) == EXPECTED_LLM_ANSWER_SCHEMA


def test_claim_rejects_non_null_source() -> None:
    with pytest.raises(ValidationError):
        Claim(text="x", source="abc")  # type: ignore[arg-type]


def test_claim_requires_source_field() -> None:
    with pytest.raises(ValidationError):
        Claim.model_validate({"text": "x"})


def test_claim_accepts_null_source() -> None:
    assert Claim(text="x", source=None).source is None


@pytest.mark.parametrize("model", [Claim, LLMAnswer, ChatResponse])
def test_models_forbid_extra_fields(model: type[BaseModel]) -> None:
    with pytest.raises(ValidationError, match="extra"):
        model.model_validate({"unexpected": 1})


def test_chat_response_rejects_unknown_answer_type() -> None:
    with pytest.raises(ValidationError):
        ChatResponse.model_validate(
            {
                "schema_version": "1.0",
                "request_id": "r",
                "conversation_id": "c",
                "answer_type": "poem",
                "category": "none",
                "answer": "a",
                "claims": [],
                "notices": [],
            }
        )


class _Inner(Strict):
    title: str  # a field named like a schema keyword must survive
    note: str | None


class _Outer(Strict):
    inner: _Inner | None
    items: list[_Inner]
    count: int = 3


def test_strict_schema_handles_nesting_nullables_and_defaults() -> None:
    inner = {
        "type": "object",
        "properties": {"title": {"type": "string"}, "note": {"type": ["string", "null"]}},
        "required": ["title", "note"],
        "additionalProperties": False,
    }
    assert to_groq_strict(_Outer) == {
        "type": "object",
        "properties": {
            "inner": {"anyOf": [inner, {"type": "null"}]},
            "items": {"type": "array", "items": inner},
            "count": {"type": "integer"},
        },
        "required": ["inner", "items", "count"],
        "additionalProperties": False,
    }
