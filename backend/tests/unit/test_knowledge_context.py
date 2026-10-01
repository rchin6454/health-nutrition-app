"""The <context> block (architecture §5.5, §9.3) and the `unverified_number` soft check (§6.4)."""

import pytest

from app.pipeline.prompt_builder import EMPTY_CONTEXT, build_answer_messages, format_context
from app.pipeline.validate import unsupported_without_context, unverified_numbers
from app.schemas.analysis import QuestionAnalysis
from app.schemas.answer import Claim, LLMAnswer
from app.schemas.context import EMPTY_BUNDLE, ContextBundle, Fact, Passage
from tests.conftest import analysis_json

PANEER_FACT = Fact(
    id="F1",
    kind="nutrient",
    content="Paneer (raw), per 100 g: energy 258 kcal, protein 18.9 g, fat 14.8 g.",
    origin="IFCT 2017 (ICMR-NIN)",
)
ROTI_FACT = Fact(
    id="F2",
    kind="nutrient",
    content="Roti / chapati (cooked), 2 rotis ≈ 80 g: energy 239 kcal, protein 6.28 g.",
    origin="USDA FoodData Central",
)
BUNDLE = ContextBundle(
    facts=[PANEER_FACT, ROTI_FACT],
    passages=[],
    unresolved_entities=["dragon fruit"],
    assumptions=["1 medium roti is taken as about 40 g."],
)


def test_context_lists_facts_assumptions_and_unresolved_foods() -> None:
    assert format_context(BUNDLE) == (
        "[F1] Paneer (raw), per 100 g: energy 258 kcal, protein 18.9 g, fat 14.8 g.\n"
        "[F2] Roti / chapati (cooked), 2 rotis ≈ 80 g: energy 239 kcal, protein 6.28 g.\n"
        "Assumptions:\n"
        "- 1 medium roti is taken as about 40 g.\n"
        "No verified data found for: dragon fruit"
    )


def test_context_never_names_the_dataset() -> None:
    text = format_context(BUNDLE)
    assert "IFCT" not in text
    assert "USDA" not in text


def test_empty_context_says_no_verified_data() -> None:
    assert format_context(None) == EMPTY_CONTEXT
    assert format_context(EMPTY_BUNDLE) == EMPTY_CONTEXT
    only_unresolved = EMPTY_BUNDLE.model_copy(update={"unresolved_entities": ["kale chips"]})
    assert format_context(only_unresolved) == (
        f"{EMPTY_CONTEXT}\nNo verified data found for: kale chips"
    )


def test_answer_messages_carry_the_context_block() -> None:
    analysis = QuestionAnalysis.model_validate_json(analysis_json())
    final = build_answer_messages("Protein in paneer?", [], analysis, BUNDLE)[-1]["content"]
    assert f"<context>\n{format_context(BUNDLE)}\n</context>" in final


def _answer(*claims: str) -> LLMAnswer:
    return LLMAnswer(answer="…", claims=[Claim(text=c, source=None) for c in claims])


@pytest.mark.parametrize(
    "claim",
    [
        "Paneer has 18.9 g of protein per 100 g.",
        "Paneer has about 19 g protein per 100 g.",  # rounded
        "100g of paneer gives 258 kcal.",
        "Two rotis provide about 239 kcal.",
        "Paneer is a good source of protein.",  # no numbers
        "Cooked food should not stay out for more than 2 hours.",  # not a nutrient number
        "Keep it below 5 °C.",
    ],
)
def test_numbers_found_in_the_facts_are_verified(claim: str) -> None:
    assert unverified_numbers(_answer(claim), BUNDLE, "How much protein is in paneer?") == []


def test_numbers_from_the_question_are_verified() -> None:
    answer = _answer("A 150 g serving is a common portion.")
    assert unverified_numbers(answer, BUNDLE, "Protein in 150 g paneer?") == []


def test_numbers_not_in_the_context_are_reported() -> None:
    answer = _answer(
        "Paneer has 18.9 g protein per 100 g.",
        "Paneer contains 208 mg of calcium per 100 g.",
        "A roti has 1,200 kcal.",
    )
    assert unverified_numbers(answer, BUNDLE, "Is paneer healthy?") == [
        "claims[1]: 208 mg",
        "claims[2]: 1200 kcal",
    ]


def test_every_number_is_unverified_when_the_context_is_empty() -> None:
    answer = _answer("Paneer has 18 g protein per 100 g.")
    assert unverified_numbers(answer, EMPTY_BUNDLE, "Is paneer healthy?") == ["claims[0]: 18 g"]


# --- Passages (Phase 4) ---

PASSAGE = Passage(
    id="P1",
    text="Storing food — Power cuts: A closed fridge keeps food cold for about 4 hours.",
    origin="USDA FSIS power-outage guidance",
    score=0.78,
)


def test_passages_follow_the_facts_without_their_origin() -> None:
    bundle = BUNDLE.model_copy(update={"passages": [PASSAGE]})
    lines = format_context(bundle).splitlines()
    assert lines[2] == f"[P1] {PASSAGE.text}"
    assert "USDA" not in format_context(bundle)


def test_passages_alone_are_not_an_empty_context() -> None:
    bundle = EMPTY_BUNDLE.model_copy(update={"passages": [PASSAGE]})
    assert EMPTY_CONTEXT not in format_context(bundle)


def test_numbers_in_passages_count_as_known() -> None:
    bundle = EMPTY_BUNDLE.model_copy(
        update={"passages": [PASSAGE.model_copy(update={"text": "Limit salt to 5 g a day."})]}
    )
    answer = _answer("Adults should keep salt under 5 g a day.")
    assert unverified_numbers(answer, bundle, "How much salt?") == []


# --- unsupported_without_context (§6.4) ---


def _answer_text(text: str) -> LLMAnswer:
    return LLMAnswer(answer=text, claims=[Claim(text="Rice should be refrigerated.", source=None)])


@pytest.mark.parametrize("context", [None, EMPTY_BUNDLE])
def test_unhedged_answer_without_context_is_flagged(context: ContextBundle | None) -> None:
    detail = unsupported_without_context(_answer_text("Refrigerate rice within 1 hour."), context)
    assert detail is not None
    assert "1 claim(s)" in detail


@pytest.mark.parametrize(
    "text",
    [
        "Refrigerate rice. These details could not be verified against reference data.",
        "Refrigerate rice (I couldn't check this against verified data).",
        "This is general guidance; it has not been verified.",
        "Values are approximate.",
    ],
)
def test_hedged_answer_without_context_is_fine(text: str) -> None:
    assert unsupported_without_context(_answer_text(text), EMPTY_BUNDLE) is None


def test_answers_with_context_are_not_checked() -> None:
    assert unsupported_without_context(_answer_text("Paneer has protein."), BUNDLE) is None
