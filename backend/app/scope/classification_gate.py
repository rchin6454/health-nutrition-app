"""Gate 2: decides from the validated `QuestionAnalysis` whether the answer call runs (§7)."""

from dataclasses import dataclass
from typing import Literal

from app.schemas.analysis import QuestionAnalysis

ALLOWED_CATEGORIES = frozenset({"nutrition", "food_safety", "general_food", "mixed"})

MEDICATION_REFERRAL = (
    "Questions that involve medicines, supplement doses or how food interacts with a medicine "
    "need someone who knows your health history. Please ask your doctor or pharmacist.\n\n"
    "I'm happy to help with general food, nutrition and food-safety questions."
)

Action = Literal["answer", "out_of_scope", "invalid_category", "referral", "clarify"]


@dataclass(frozen=True)
class GateDecision:
    action: Action
    reply: str | None = None  # the code-owned answer text for non-answers


def check_classification(analysis: QuestionAnalysis) -> GateDecision:
    if analysis.category == "out_of_scope":
        return GateDecision("out_of_scope")
    if analysis.category not in ALLOWED_CATEGORIES:
        return GateDecision("invalid_category")
    # Before clarification: a medicine question gets a referral, not a follow-up question.
    if "medication" in analysis.risk_flags:
        return GateDecision("referral", MEDICATION_REFERRAL)
    if analysis.needs_clarification:
        return GateDecision("clarify", analysis.clarifying_question)
    return GateDecision("answer")
