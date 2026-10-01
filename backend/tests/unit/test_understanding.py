"""The understanding call's schema, validation and prompts (architecture §5.3, §5.5)."""

import json
from datetime import UTC, datetime

from app.llm.strict_schema import to_groq_strict
from app.pipeline.prompt_builder import (
    EMPTY_CONTEXT,
    build_answer_messages,
    build_understanding_messages,
    format_analysis,
)
from app.pipeline.understanding import QUESTION_ANALYSIS_SCHEMA, validate_analysis
from app.pipeline.validate import ValidationFailure
from app.prompts import ANSWER_SYSTEM_PROMPT, UNDERSTANDING_SYSTEM_PROMPT
from app.schemas.analysis import QuestionAnalysis
from app.store.conversations import StoredMessage
from tests.conftest import analysis_json


def _walk_objects(node: object) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found.extend(_walk_objects(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_objects(item))
    return found


def test_question_analysis_schema_is_strict() -> None:
    assert QUESTION_ANALYSIS_SCHEMA == to_groq_strict(QuestionAnalysis)
    objects = _walk_objects(QUESTION_ANALYSIS_SCHEMA)
    assert len(objects) == 4  # analysis, entities, quantity, storage
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])  # type: ignore[call-overload]
    assert "$defs" not in json.dumps(QUESTION_ANALYSIS_SCHEMA)
    assert QUESTION_ANALYSIS_SCHEMA["properties"]["category"]["enum"] == [
        "nutrition",
        "food_safety",
        "general_food",
        "mixed",
        "out_of_scope",
    ]


def test_valid_analysis_parses() -> None:
    result = validate_analysis(analysis_json())
    assert isinstance(result, QuestionAnalysis)
    assert result.category == "nutrition"


def test_storage_context_parses() -> None:
    raw = analysis_json(
        category="food_safety",
        entities={
            "foods": ["cooked rice"],
            "nutrients": [],
            "quantities": [{"value": 1, "unit": "katori"}],
            "storage": {"location": "room_temp", "duration": "overnight", "state": "cooked"},
            "cooking_methods": [],
        },
    )
    result = validate_analysis(raw)
    assert isinstance(result, QuestionAnalysis)
    assert result.entities.storage is not None
    assert result.entities.storage.duration == "overnight"


def test_invalid_output_is_a_failure_and_never_repaired() -> None:
    for raw in [None, '{"category": "nutrition"', analysis_json(category="astrology")]:
        result = validate_analysis(raw)
        assert isinstance(result, ValidationFailure)
        assert result.failure_type == "schema_validation_failed"


def test_unknown_risk_flag_is_rejected() -> None:
    result = validate_analysis(analysis_json(risk_flags=["zodiac"]))
    assert isinstance(result, ValidationFailure)


def test_extra_field_is_rejected() -> None:
    raw = json.loads(analysis_json())
    raw["answer"] = "sneaky"
    result = validate_analysis(json.dumps(raw))
    assert isinstance(result, ValidationFailure)


def test_clarification_without_a_question_is_a_failure() -> None:
    for question in [None, "  "]:
        result = validate_analysis(
            analysis_json(needs_clarification=True, clarifying_question=question)
        )
        assert isinstance(result, ValidationFailure)
        assert result.failure_type == "missing_clarifying_question"


def _history() -> list[StoredMessage]:
    now = datetime.now(UTC)
    return [
        StoredMessage(1, now, "r1", "user", {"text": "Calories in rice?"}),
        StoredMessage(2, now, "r1", "assistant", {"answer_type": "answer", "answer": "About 130."}),
        StoredMessage(3, now, "r2", "user", {"text": "and roti?"}),
        StoredMessage(4, now, "r2", "assistant", {"answer_type": "error", "answer": "Sorry"}),
    ]


def test_understanding_messages() -> None:
    messages = build_understanding_messages("what about brown rice?", _history())
    assert messages == [
        {"role": "system", "content": UNDERSTANDING_SYSTEM_PROMPT},
        {"role": "user", "content": "Calories in rice?"},
        {"role": "assistant", "content": "About 130."},
        {"role": "user", "content": "and roti?"},  # the error reply is left out
        {"role": "user", "content": "<user_question>\nwhat about brown rice?\n</user_question>"},
    ]


def test_answer_messages_have_analysis_context_and_question_blocks() -> None:
    analysis = QuestionAnalysis.model_validate_json(analysis_json(user_context=["vegetarian"]))
    messages = build_answer_messages("Is brown rice healthier?", _history(), analysis)

    assert messages[0] == {"role": "system", "content": ANSWER_SYSTEM_PROMPT}
    assert len(messages) == 5
    final = messages[-1]["content"]
    assert final.startswith("<question_analysis>\ncategory: nutrition\n")
    assert 'user_context: ["vegetarian"]' in final
    assert f"<context>\n{EMPTY_CONTEXT}\n</context>" in final
    assert final.endswith("<user_question>\nIs brown rice healthier?\n</user_question>")


def test_format_analysis_includes_storage() -> None:
    analysis = QuestionAnalysis.model_validate_json(
        analysis_json(
            entities={
                "foods": ["milk"],
                "nutrients": [],
                "quantities": [],
                "storage": {"location": "room_temp", "duration": "4 hours", "state": None},
                "cooking_methods": [],
            }
        )
    )
    text = format_analysis(analysis)
    assert 'storage: {"location": "room_temp", "duration": "4 hours", "state": null}' in text
    assert 'foods: ["milk"]' in text
