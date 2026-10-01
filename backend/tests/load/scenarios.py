"""Questions the load test sends, with the analysis the stub model returns for each."""

from typing import Any


def _analysis(
    category: str,
    question_type: str,
    foods: list[str],
    *,
    nutrients: list[str] | None = None,
    quantities: list[dict[str, Any]] | None = None,
    storage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "intent_summary": f"{question_type} about {', '.join(foods) or 'food'}",
        "category": category,
        "question_type": question_type,
        "entities": {
            "foods": foods,
            "nutrients": nutrients or [],
            "quantities": quantities or [],
            "storage": storage,
            "cooking_methods": [],
        },
        "user_context": [],
        "needs_clarification": False,
        "clarifying_question": None,
        "risk_flags": [],
    }


# question → the stub's understanding output. Questions the input gate blocks have no entry.
SCENARIOS: dict[str, dict[str, Any] | None] = {
    "How much protein is there in 100g of paneer?": _analysis(
        "nutrition", "lookup", ["paneer"], nutrients=["protein"],
        quantities=[{"value": 100, "unit": "g"}],
    ),
    "How many calories are in 2 rotis?": _analysis(
        "nutrition", "lookup", ["roti"], nutrients=["energy"],
        quantities=[{"value": 2, "unit": "roti"}],
    ),
    "Is brown rice healthier than white rice?": _analysis(
        "nutrition", "comparison", ["brown rice", "white rice"]
    ),
    "What foods are high in iron?": _analysis(
        "nutrition", "recommendation", [], nutrients=["iron"]
    ),
    "Can I eat cooked rice that was left outside overnight?": _analysis(
        "food_safety", "safety_check", ["cooked rice"],
        storage={"location": "room_temp", "duration": "overnight", "state": "cooked"},
    ),
    "How long can chicken be stored in the refrigerator?": _analysis(
        "food_safety", "how_to", ["chicken"],
        storage={"location": "fridge", "duration": None, "state": None},
    ),
    "Is paneer healthy and how long does it last in the fridge?": _analysis(
        "mixed", "safety_check", ["paneer"],
        storage={"location": "fridge", "duration": None, "state": None},
    ),
    "Write a poem about cars": _analysis("out_of_scope", "how_to", []),
    "Dose of metformin?": None,  # blocked by the input gate: no model call
}  # fmt: skip

ANSWER = {
    "answer": (
        "**Load-test answer.** This text is returned by the stub model, not by Groq, and could "
        "not be verified."
    ),
    "claims": [{"text": "This is a stubbed claim used only for load testing.", "source": None}],
}
