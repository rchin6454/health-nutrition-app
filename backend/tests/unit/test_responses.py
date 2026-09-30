from app.responses import DISCLAIMER, build_answer_response, build_error_response
from app.schemas.answer import ChatResponse, Claim, LLMAnswer


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
