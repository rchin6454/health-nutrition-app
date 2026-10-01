"""Gate 3: runs after the answer call (architecture §7).

It checks the invariants, sets `category` from the analysis (the model never picks it) and adds
the code-owned notices. The model's `answer` and `claims` are never edited.
"""

from pydantic import ValidationError

from app.pipeline.validate import ValidationFailure, check_invariants
from app.responses import (
    DISCLAIMER,
    EMERGENCY_NOTICE,
    HIGH_RISK_NOTICE,
    SYMPTOMS_NOTICE,
    build_answer_response,
)
from app.schemas.analysis import QuestionAnalysis
from app.schemas.answer import ChatResponse, LLMAnswer


def notices_for(*, emergency: bool, analysis: QuestionAnalysis | None) -> list[str]:
    """Notices for any response in a turn: the most urgent first, the disclaimer always last."""
    flags = analysis.risk_flags if analysis else []
    notices: list[str] = []
    if emergency:
        notices.append(EMERGENCY_NOTICE)
    elif "symptoms" in flags:
        notices.append(SYMPTOMS_NOTICE)
    if "high_risk_group" in flags:
        notices.append(HIGH_RISK_NOTICE)
    notices.append(DISCLAIMER)
    return notices


def finalize_answer(
    *,
    request_id: str,
    conversation_id: str,
    llm_answer: LLMAnswer,
    analysis: QuestionAnalysis,
    emergency: bool,
) -> ChatResponse | ValidationFailure:
    failure = check_invariants(llm_answer)
    if failure:
        return failure
    try:
        return build_answer_response(
            request_id=request_id,
            conversation_id=conversation_id,
            llm_answer=llm_answer,
            category=analysis.category,
            notices=notices_for(emergency=emergency, analysis=analysis),
        )
    except ValidationError as exc:
        return ValidationFailure("response_build_failed", str(exc))
