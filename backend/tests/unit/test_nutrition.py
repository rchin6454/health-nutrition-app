"""Nutrition fact building (architecture §5.4): scaling maths, comparisons, recommendations."""

import pytest

from app.knowledge.entity_resolver import FoodEntry
from app.knowledge.nutrients import format_amount, normalize_nutrient
from app.knowledge.nutrition import (
    comparison_fact,
    diet_filter,
    nutrient_list,
    pair_quantities,
    per_100g_fact,
    recommendation_fact,
    requested_nutrients,
    scaled_fact,
    variety_base,
)
from app.schemas.analysis import Quantity

PANEER = FoodEntry("ifct:L003", "Paneer", "IFCT2017", "raw")
ROTI = FoodEntry("usda:2707713", "Roti / chapati", "USDA_FDC", "cooked")
BROWN = FoodEntry("ifct:A013", "Rice, raw, brown", "IFCT2017", "raw")
WHITE = FoodEntry("ifct:A015", "Rice, raw, milled", "IFCT2017", "raw")

PANEER_VALUES = {"energy_kcal": 257.89, "protein": 18.86, "fat": 14.78, "iron": 0.9}
ROTI_VALUES = {"energy_kcal": 299.0, "protein": 7.85, "fat": 9.2, "carbohydrate": 46.4}


@pytest.mark.parametrize(
    ("amount", "text"),
    [(257.89, "258"), (18.86, "18.9"), (2.95, "2.95"), (0.4, "0.4"), (10.0, "10"), (0.0, "0")],
)
def test_format_amount(amount: float, text: str) -> None:
    assert format_amount(amount) == text


@pytest.mark.parametrize(
    ("name", "canonical"),
    [
        ("Protein", ("protein",)),
        ("calories", ("energy_kcal",)),
        ("energy", ("energy_kcal",)),
        ("fibre", ("fiber",)),
        ("Vitamin C", ("vitamin_c",)),
        ("vitamin a", ("retinol", "beta_carotene")),
        ("carbs", ("carbohydrate",)),
        ("vitamin b12", ()),  # no data for it
    ],
)
def test_normalize_nutrient(name: str, canonical: tuple[str, ...]) -> None:
    assert normalize_nutrient(name) == canonical


def test_requested_nutrients_dedupes_and_reports_unknown_ones() -> None:
    assert requested_nutrients(["protein", "Protein", "b12", "calories"]) == (
        ["protein", "energy_kcal"],
        ["b12"],
    )


def test_per_100g_fact_lists_only_nutrients_with_values() -> None:
    assert per_100g_fact(PANEER, PANEER_VALUES, ["protein", "fiber", "energy_kcal"]) == (
        "Paneer (raw), per 100 g: protein 18.9 g, energy 258 kcal."
    )
    assert per_100g_fact(PANEER, PANEER_VALUES, ["fiber"]) is None


def test_scaling_to_two_rotis() -> None:
    # 2 rotis ≈ 80 g → 0.8 times the per-100 g values.
    fact = scaled_fact(ROTI, ROTI_VALUES, ["energy_kcal", "protein"], 80, "2 rotis ≈ 80 g")
    assert fact == "Roti / chapati (cooked), 2 rotis ≈ 80 g: energy 239 kcal, protein 6.28 g."


def test_scaling_to_one_katori_palak() -> None:
    spinach = FoodEntry("ifct:C033", "Spinach", "IFCT2017", "raw")
    values = {"iron": 2.95}
    assert nutrient_list(values, ["iron"], scale=100 / 100) == "iron 2.95 mg"
    fact = scaled_fact(spinach, values, ["iron"], 100, "1 katori ≈ 100 g")
    assert fact == "Spinach (raw), 1 katori ≈ 100 g: iron 2.95 mg."


def test_scaling_small_amounts() -> None:
    assert nutrient_list(PANEER_VALUES, ["protein", "energy_kcal"], scale=0.3) == (
        "protein 5.66 g, energy 77.4 kcal"
    )


def test_comparison_fact_uses_nutrients_both_foods_have() -> None:
    values = {
        BROWN.id: {"fiber": 4.43, "protein": 9.16, "thiamine": 0.29},
        WHITE.id: {"fiber": 2.81, "protein": 7.94},
    }
    assert comparison_fact([BROWN, WHITE], values, ["fiber", "protein", "thiamine"]) == (
        "Per 100 g, Rice, raw, brown (raw) vs Rice, raw, milled (raw): "
        "fibre 4.43 g vs 2.81 g; protein 9.16 g vs 7.94 g."
    )
    assert comparison_fact([BROWN, WHITE], values, ["thiamine"]) is None


def test_recommendation_fact_ranks_foods() -> None:
    fact = recommendation_fact(
        "iron", diet_filter([]), [("Garden cress, seeds", 17.2), ("Horse gram, whole", 8.76)]
    )
    assert fact == (
        "Vegetarian foods highest in iron per 100 g (raw, as purchased): "
        "1. Garden cress, seeds 17.2 mg; 2. Horse gram, whole 8.76 mg."
    )


@pytest.mark.parametrize(
    ("context", "label", "diets", "excluded"),
    [
        ([], "vegetarian", ("veg",), ()),
        (["diabetic"], "vegetarian", ("veg",), ()),
        (["non-vegetarian"], "", ("veg", "egg", "nonveg"), ()),
        (["Non_Vegetarian"], "", ("veg", "egg", "nonveg"), ()),
        (["eggetarian"], "vegetarian or egg", ("veg", "egg"), ()),
        (["vegan"], "vegan", ("veg",), ("Milk and Milk Products",)),
        (["jain"], "Jain vegetarian", ("veg",), ("Roots and Tubers",)),
    ],
)
def test_recommendations_are_vegetarian_by_default(
    context: list[str], label: str, diets: tuple[str, ...], excluded: tuple[str, ...]
) -> None:
    diet = diet_filter(context)
    assert (diet.label, diet.diets, diet.excluded_groups) == (label, diets, excluded)


@pytest.mark.parametrize(
    ("name", "base"),
    [
        ("Brinjal 12", "brinjal"),
        ("Brinjal - all varieties", "brinjal"),
        ("Rajmah, red", "rajmah"),
        ("Chillies, green-3", "chillies"),
        ("Spinach", "spinach"),
    ],
)
def test_variety_base_collapses_varieties(name: str, base: str) -> None:
    assert variety_base(name) == base


def test_pair_quantities() -> None:
    two_rotis = Quantity(value=2, unit="roti")
    katori = Quantity(value=1, unit="katori")
    assert pair_quantities(["roti"], []) == {}
    assert pair_quantities(["roti"], [two_rotis]) == {"roti": two_rotis}
    assert pair_quantities(["roti", "dal"], [two_rotis, katori]) == {
        "roti": two_rotis,
        "dal": katori,
    }
    assert pair_quantities(["roti", "dal"], [two_rotis]) is None  # ambiguous
