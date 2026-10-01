import pytest

from app.responses import (
    DISCLAIMER,
    EMERGENCY_NOTICE,
    OUT_OF_SCOPE_REPLY,
    build_answer_response,
    build_clarification_response,
    build_error_response,
    build_out_of_scope_response,
    build_referral_response,
)
from app.schemas.answer import ChatResponse, Claim, LLMAnswer
from app.scope.blocked_topics import (
    ALCOHOL_OR_DRUGS,
    DIAGNOSIS,
    EATING_DISORDER,
    MEDICATION_DOSING,
    WEIGHT_LOSS_DRUGS,
)
from app.scope.classification_gate import MEDICATION_REFERRAL
from app.scope.input_gate import TOO_LONG_REPLY, TOO_SHORT_REPLY


def _roundtrip(response: ChatResponse) -> ChatResponse:
    return ChatResponse.model_validate_json(response.model_dump_json())


def test_answer_response_is_valid_and_passes_model_output_through() -> None:
    llm = LLMAnswer(
        answer="**Yes.** Brown rice has more fibre.", claims=[Claim(text="t", source=None)]
    )
    response = build_answer_response(request_id="r1", conversation_id="c1", llm_answer=llm)

    assert _roundtrip(response) == response
    assert response.answer_type == "answer"
    assert response.category == "none"
    assert response.answer == llm.answer
    assert response.claims == llm.claims
    assert response.notices == [DISCLAIMER]


def test_error_response_is_valid_and_includes_request_id() -> None:
    response = build_error_response(request_id="r2", conversation_id="c2")

    assert _roundtrip(response) == response
    assert response.answer_type == "error"
    assert response.claims == []
    assert "r2" in response.answer
    assert response.notices == [DISCLAIMER]


def test_clarification_response() -> None:
    response = build_clarification_response(
        request_id="r3",
        conversation_id="c3",
        question="Which food, and how was it stored?",
        category="food_safety",
    )

    assert _roundtrip(response) == response
    assert response.answer_type == "clarification"
    assert response.category == "food_safety"
    assert response.answer == "Which food, and how was it stored?"
    assert response.claims == []


def test_out_of_scope_response() -> None:
    response = build_out_of_scope_response(request_id="r4", conversation_id="c4")

    assert _roundtrip(response) == response
    assert response.answer_type == "out_of_scope"
    assert response.category == "out_of_scope"
    assert response.answer == OUT_OF_SCOPE_REPLY
    assert response.claims == []


@pytest.mark.parametrize(
    "message",
    [
        MEDICATION_REFERRAL,
        TOO_SHORT_REPLY,
        TOO_LONG_REPLY,
        *(
            t.referral
            for t in [
                MEDICATION_DOSING,
                DIAGNOSIS,
                WEIGHT_LOSS_DRUGS,
                EATING_DISORDER,
                ALCOHOL_OR_DRUGS,
            ]
        ),
    ],
)
def test_every_code_written_referral_is_a_valid_response(message: str) -> None:
    response = build_referral_response(request_id="r5", conversation_id="c5", message=message)

    assert _roundtrip(response) == response
    assert response.answer_type == "out_of_scope"
    assert response.answer == message
    assert response.claims == []


def test_notices_are_passed_through() -> None:
    notices = [EMERGENCY_NOTICE, DISCLAIMER]
    for response in [
        build_error_response(request_id="r", conversation_id="c", notices=notices),
        build_out_of_scope_response(request_id="r", conversation_id="c", notices=notices),
        build_clarification_response(
            request_id="r", conversation_id="c", question="q?", category="mixed", notices=notices
        ),
    ]:
        assert response.notices == notices
