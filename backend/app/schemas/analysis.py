"""Question analysis returned by the understanding call, model call 1 (architecture §5.3).

Every field is required (Groq strict mode); fields that may be empty are `X | None` or lists.
"""

from typing import Literal

from app.schemas.answer import Strict

Category = Literal["nutrition", "food_safety", "general_food", "mixed", "out_of_scope"]
QuestionType = Literal["lookup", "comparison", "recommendation", "safety_check", "how_to"]
RiskFlag = Literal["high_risk_group", "symptoms", "allergy", "medication"]


class Quantity(Strict):
    value: float
    unit: str  # "g", "ml", "katori", "roti", "cup", "piece"


class StorageContext(Strict):
    location: str | None  # "fridge", "freezer", "room_temp"
    duration: str | None  # "overnight", "3 days"
    state: str | None  # "raw", "cooked", "opened", "thawed"


class Entities(Strict):
    foods: list[str]  # normalized English names: "paneer", "cooked rice"
    nutrients: list[str]  # "protein", "iron"
    quantities: list[Quantity]
    storage: StorageContext | None
    cooking_methods: list[str]


class QuestionAnalysis(Strict):
    intent_summary: str
    category: Category
    question_type: QuestionType
    entities: Entities
    user_context: list[str]  # "vegetarian", "pregnant", "diabetic", "vrat"
    needs_clarification: bool
    clarifying_question: str | None
    risk_flags: list[RiskFlag]
