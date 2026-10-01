"""Gate 2 (classification) and Gate 3 (output) from architecture §7."""

import json
from typing import Any

import pytest

from app.pipeline.validate import ValidationFailure
from app.responses import DISCLAIMER, EMERGENCY_NOTICE, HIGH_RISK_NOTICE, SYMPTOMS_NOTICE
from app.schemas.analysis import QuestionAnalysis
from app.schemas.answer import ChatResponse, Claim, LLMAnswer
from app.scope.classification_gate import MEDICATION_REFERRAL, check_classification
from app.scope.output_gate import finalize_answer, notices_for
from tests.conftest import analysis_json


def _analysis(**overrides: Any) -> QuestionAnalysis:
    return QuestionAnalysis.model_validate_json(analysis_json(**overrides))


# --- Gate 2 ---


@pytest.mark.parametrize("category", ["nutrition", "food_safety", "general_food", "mixed"])
def test_in_scope_categories_go_to_the_answer_call(category: str) -> None:
    assert check_classification(_analysis(category=category)).action == "answer"


def test_out_of_scope() -> None:
    assert check_classification(_analysis(category="out_of_scope")).action == "out_of_scope"


def test_unknown_category_is_a_failure() -> None:
    # The strict schema makes this unreachable from the model; the gate still checks it.
    analysis = _analysis().model_copy(update={"category": "astrology"})
    assert check_classification(analysis).action == "invalid_category"


def test_clarification_returns_the_models_question() -> None:
    decision = check_classification(
        _analysis(needs_clarification=True, clarifying_question="Which food, stored where?")
    )
    assert decision.action == "clarify"
    assert decision.reply == "Which food, stored where?"


def test_medication_flag_gets_a_referral_even_when_clarification_is_needed() -> None:
    decision = check_classification(
        _analysis(risk_flags=["medication"], needs_clarification=True, clarifying_question="Which?")
    )
    assert decision.action == "referral"
    assert decision.reply == MEDICATION_REFERRAL


def test_out_of_scope_wins_over_everything() -> None:
    decision = check_classification(_analysis(category="out_of_scope", risk_flags=["medication"]))
    assert decision.action == "out_of_scope"


# --- Gate 3 ---


def _llm(answer: str = "**Yes.** Fine.", claims: int = 1) -> LLMAnswer:
    return LLMAnswer(
        answer=answer, claims=[Claim(text=f"c{i}", source=None) for i in range(claims)]
    )


@pytest.mark.parametrize(
    ("emergency", "risk_flags", "expected"),
    [
        (False, [], [DISCLAIMER]),
        (True, [], [EMERGENCY_NOTICE, DISCLAIMER]),
        (False, ["symptoms"], [SYMPTOMS_NOTICE, DISCLAIMER]),
        (True, ["symptoms"], [EMERGENCY_NOTICE, DISCLAIMER]),  # the stronger notice only
        (False, ["high_risk_group"], [HIGH_RISK_NOTICE, DISCLAIMER]),
        (
            False,
            ["symptoms", "high_risk_group", "allergy"],
            [SYMPTOMS_NOTICE, HIGH_RISK_NOTICE, DISCLAIMER],
        ),
    ],
)
def test_notices(emergency: bool, risk_flags: list[str], expected: list[str]) -> None:
    assert notices_for(emergency=emergency, analysis=_analysis(risk_flags=risk_flags)) == expected


def test_notices_without_analysis() -> None:
    assert notices_for(emergency=False, analysis=None) == [DISCLAIMER]
    assert notices_for(emergency=True, analysis=None) == [EMERGENCY_NOTICE, DISCLAIMER]


def test_output_gate_sets_category_from_analysis_and_keeps_model_output() -> None:
    llm = _llm()
    response = finalize_answer(
        request_id="r",
        conversation_id="c",
        llm_answer=llm,
        analysis=_analysis(category="food_safety", risk_flags=["high_risk_group"]),
        emergency=False,
    )
    assert isinstance(response, ChatResponse)
    assert response.category == "food_safety"
    assert response.answer == llm.answer
    assert response.claims == llm.claims
    assert response.notices == [HIGH_RISK_NOTICE, DISCLAIMER]


@pytest.mark.parametrize(
    ("llm", "failure_type"),
    [
        (_llm(answer="  "), "empty_answer"),
        (_llm(claims=0), "empty_claims"),
        (_llm(claims=9), "length_exceeded"),
        (_llm(answer="x" * 1501), "length_exceeded"),
    ],
)
def test_output_gate_runs_the_invariants(llm: LLMAnswer, failure_type: str) -> None:
    result = finalize_answer(
        request_id="r", conversation_id="c", llm_answer=llm, analysis=_analysis(), emergency=False
    )
    assert isinstance(result, ValidationFailure)
    assert result.failure_type == failure_type


def test_analysis_fixture_is_valid_json() -> None:
    assert json.loads(analysis_json())["category"] == "nutrition"
